---
name: add-pattern
description: Add a new orchestration pattern to the project
disable-model-invocation: true
---

Add a new orchestration pattern: $ARGUMENTS

Follow these steps:

1. Read `shared/state.py` and `shared/agents.py` to understand the state model and existing agent nodes
2. Read an existing pattern in `patterns/` (start with `hierarchical.py`) to understand the structure
3. Create `patterns/<name>.py` with:
   - A `StateGraph(AgentState)` with your routing topology
   - An exported `run_<name>(topic: str) -> dict` function
   - The returned dict must contain: `final_output`, `review_score`, `iteration`, `agent_history`
4. Reuse agent nodes from `shared/agents.py` — do NOT duplicate agent logic
5. If new agent roles are needed, add the prompt to `shared/prompts.py` and the node to `shared/agents.py`
6. Add the new pattern to `run_all.py` for comparison
7. Run the new pattern individually to verify it works before running `run_all.py`
