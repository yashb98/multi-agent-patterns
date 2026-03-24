# Agents

Agent roles, state model, and LLM configuration.

## Agent Roles

### Researcher (`shared/agents.py:researcher_node`)
- Gathers facts, technical details, trends, expert opinions
- Reads: `topic`, `review_feedback` (for targeted re-research)
- Writes: `research_notes` (appends via Annotated list)

### Writer (`shared/agents.py:writer_node`)
- Transforms research into polished technical blog articles
- Reads: `topic`, `research_notes`, `review_feedback`
- Writes: `draft` (replaces previous draft)

### Reviewer (`shared/agents.py:reviewer_node`)
- Evaluates quality, returns structured JSON with scores
- Reads: `draft`, `topic`
- Writes: `review_feedback`, `review_score` (0-10), `review_passed`

### Supervisor (`patterns/hierarchical.py`)
- Routes tasks to agents based on current state
- Two modes: rule-based (deterministic) and LLM-based (flexible)
- Only used in the hierarchical pattern

### Debate Moderator (`patterns/peer_debate.py`)
- Synthesizes debate positions into final output
- Only used in the peer debate pattern

## LLM Configuration

All LLM calls go through `get_llm()` in `shared/agents.py`:

```python
get_llm(temperature=0.7, model="gpt-4o-mini")
```

Never instantiate `ChatOpenAI` directly elsewhere. This is the single source of truth for model config.

## State Model (AgentState)

Defined in `shared/state.py`. The shared whiteboard for all inter-agent communication.

```python
AgentState(TypedDict):
    topic: str                              # Input (immutable)
    research_notes: Annotated[list, add]    # Append-only
    draft: str                              # Replace
    review_feedback: Optional[str]          # Replace
    review_score: float                     # Replace (0-10)
    review_passed: bool                     # Replace
    iteration: int                          # Replace
    current_agent: str                      # Replace
    agent_history: Annotated[list, add]     # Append-only
    pending_tasks: list[dict]               # Replace (swarm only)
    final_output: str                       # Replace
```

### Key Behaviors

- `Annotated[list, operator.add]` fields **accumulate** across agents
- Regular fields are **overwritten** on each update
- Agents return partial dicts — LangGraph merges automatically
- `create_initial_state(topic)` provides clean initialization

## Prompts

All system prompts live in `shared/prompts.py`:

| Constant | Used By | Output Format |
|----------|---------|---------------|
| `RESEARCHER_PROMPT` | Researcher | Free-form research notes |
| `WRITER_PROMPT` | Writer | Markdown blog article |
| `REVIEWER_PROMPT` | Reviewer | Structured JSON with scores |
| `SUPERVISOR_PROMPT` | Supervisor | Next agent name |
| `DEBATE_MODERATOR_PROMPT` | Moderator | Synthesized final output |

Each prompt includes role definition, constraints, and explicit output format requirements.

## Pattern Topologies

### Hierarchical Supervisor
```
         ┌──────────┐
    ┌───►│Supervisor│◄───┐
    │    └────┬─────┘    │
    │    ┌────┼─────┐    │
    │    ▼    ▼     ▼    │
  ┌─┴──┐ ┌──┴──┐ ┌─┴───┐
  │Res. │ │Writ.│ │Rev. │
  └─────┘ └─────┘ └─────┘
```

### Peer Debate
```
Round 1:  Researcher → Writer → Reviewer
Round 2+: All agents cross-critique each other
```

### Dynamic Swarm
```
Task Analyzer → Priority Queue → Task Executor → Agent dispatch
      ▲                                              │
      └────────────── Re-analyze ─────────────────────┘
```

### Enhanced Swarm
Dynamic Swarm + agent factory + GRPO sampling + persona evolution + experience memory.
