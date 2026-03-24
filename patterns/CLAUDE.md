# patterns/

Orchestration pattern implementations. Each file defines a complete LangGraph `StateGraph` with its own topology, routing logic, and convergence rules.

## Pattern Contracts

Every pattern module exports a `run_<name>(topic: str) -> dict` function that:
1. Creates initial `AgentState` with the given topic
2. Builds and compiles a `StateGraph`
3. Invokes the graph and returns the final state dict

The returned dict always contains: `final_output`, `review_score`, `iteration`, `agent_history`.

## Files

### hierarchical.py
- **Topology**: Hub-and-spoke. Supervisor node routes to researcher/writer/reviewer.
- **Two supervisor modes**: Rule-based (deterministic `if/else`) and LLM-based (supervisor prompt decides next agent).
- **Edges**: `supervisor → {researcher, writer, reviewer, FINISH}` via conditional edge.
- **Comparable to**: AutoGen GroupChatManager, CrewAI sequential mode.

### peer_debate.py
- **Topology**: Sequential first round, then debate rounds with cross-critique.
- **Round 1**: `researcher → writer → reviewer` (pipeline).
- **Round 2+**: All agents see each other's outputs and critique. Agents can argue back.
- **Convergence**: Score improvement threshold + patience counter.
- **Comparable to**: Society of Mind, multi-agent debate literature.

### dynamic_swarm.py
- **Topology**: Task Analyzer → priority queue → Task Executor dispatches dynamically.
- **Key feature**: Re-analysis after each task — new tasks can be discovered at runtime.
- **Comparable to**: OpenAI Swarm framework.

### enhanced_swarm.py
- **Topology**: Same as dynamic_swarm but integrates all `shared/` innovations.
- **Features**: Dynamic agent factory, GRPO group sampling, persona evolution, experience-aware convergence.
- **Global experience memory** persists across runs via `ExperienceMemory`.

## Adding a New Pattern

1. Create `patterns/new_pattern.py`
2. Import agents from `shared/agents.py` and state from `shared/state.py`
3. Build a `StateGraph(AgentState)` with your topology
4. Export `run_new_pattern(topic: str) -> dict`
5. Add it to `run_all.py` for comparison
