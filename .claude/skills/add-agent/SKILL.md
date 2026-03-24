---
name: add-agent
description: Add a new agent role to the shared agent infrastructure
disable-model-invocation: true
---

Add a new agent role: $ARGUMENTS

Follow these steps:

1. Read `shared/prompts.py` to understand existing prompt conventions
2. Read `shared/agents.py` to understand the agent node function pattern
3. Add a new system prompt constant to `shared/prompts.py`:
   - Include role definition, constraints, and explicit output format
   - Follow the naming convention: `<ROLE>_PROMPT`
4. Add a new node function to `shared/agents.py`:
   - Signature: `def <role>_node(state: AgentState) -> dict`
   - Use `get_llm()` for the LLM call — never instantiate ChatOpenAI directly
   - Return a partial dict with only the fields this agent modifies
   - Keep the function stateless — no instance variables or side effects
5. If the agent needs new state fields, add them to `shared/state.py`
   - Use `Annotated[list, operator.add]` for fields that should accumulate
6. Update `shared/__init__.py` exports if needed
7. If adding a template for the dynamic agent factory, register it in `shared/dynamic_agent_factory.py`
