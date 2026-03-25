# Agents

Two agent systems: orchestration agents (blog generation) and JobPulse agents (daily automation).

## Orchestration Agents (shared/agents.py)

### Researcher (`researcher_node`)
- Gathers facts, technical details, trends, expert opinions
- Reads: `topic`, `review_feedback` · Writes: `research_notes` (append-only)

### Writer (`writer_node`)
- Transforms research into polished articles
- Reads: `topic`, `research_notes`, `review_feedback` · Writes: `draft`

### Reviewer (`reviewer_node`)
- Evaluates quality, returns structured JSON scores
- Reads: `draft`, `topic` · Writes: `review_feedback`, `review_score`, `review_passed`

## JobPulse Agents (jobpulse/)

### Gmail Agent (`gmail_agent.py`)
- Scans inbox via Gmail API, classifies emails with LLM (SELECTED/INTERVIEW/REJECTED/OTHER)
- Sends Telegram alerts for recruiter emails, auto-extracts knowledge (company, role)
- Uses evolved persona — learns to skip automated rejections over time

### Calendar Agent (`calendar_agent.py`)
- Fetches today + tomorrow events via Google Calendar API
- Formats for Telegram display, sends upcoming reminders (2-hour window)

### GitHub Agent (`github_agent.py`)
- Fetches yesterday's commits using Commits API per-repo (not Events API)
- Fetches trending repos via GitHub Search API

### Notion Agent (`notion_agent.py`)
- Manages daily tasks (to_do blocks), creates/completes tasks
- Fuzzy matching for "mark X done" (word overlap + number normalization)
- All API calls via curl (avoids Python SSL issues)

### Budget Agent (`budget_agent.py`)
- Parses natural language ("spent 15 on lunch"), classifies category (keyword + LLM)
- Stores in SQLite, syncs Actual column to Notion Weekly Budget Sheet
- 17 categories across Income/Fixed/Variable/Savings sections

### Telegram Listener (`telegram_listener.py`)
- Long-polling daemon, instant replies (1-3s)
- Routes through Enhanced Swarm dispatcher (or flat, via env var)

### Morning Briefing (`morning_briefing.py`)
- Collects from all 6 agents, assembles Telegram message
- Evolves briefing persona after each run
- RLM synthesis when data exceeds 5K chars

## Enhanced Swarm Dispatcher (`swarm_dispatcher.py`)

Replaces flat dispatch with adaptive intelligence:
1. **Task Analyzer** — decomposes intent into priority queue
2. **Experience Memory** — retrieves learned patterns per intent
3. **Execute** — runs agents with GRPO sampling where flagged
4. **RLM Synthesis** — recursive LLM for large-context assembly
5. **Store Experience** — saves what worked for future runs

## LLM Configuration

```python
get_llm(temperature=0.7, model="gpt-4o-mini")  # shared/agents.py
```

JobPulse agents use OpenAI directly for classification (gpt-4o-mini).
RLM uses configurable backend via `RLM_BACKEND` env var.

## State Model (AgentState)

```python
AgentState(TypedDict):
    topic: str                              # Immutable input
    research_notes: Annotated[list, add]    # Append-only
    draft: str                              # Replace
    review_feedback: Optional[str]          # Replace
    review_score: float                     # 0-10
    review_passed: bool
    iteration: int
    current_agent: str
    agent_history: Annotated[list, add]     # Append-only
    pending_tasks: list[dict]               # Swarm only
    final_output: str
```

## Pattern Topologies

```
Hierarchical:   Supervisor ←→ {Researcher, Writer, Reviewer}
Peer Debate:    Round 1 pipeline → Round 2+ cross-critique
Dynamic Swarm:  Analyzer → Queue → Executor → Re-analyze loop
Enhanced Swarm: Dynamic + Factory + GRPO + Persona + RLM
```
