# shared/

Reusable agent infrastructure. Every module here is pattern-agnostic.

For detailed documentation on each module, see the docs/ files:
- @docs/agents.md — Agent roles, state model, `get_llm()`, prompts
- @docs/rules.md — Constraints, convergence rules, code conventions
- @docs/skills.md — GRPO, persona evolution, prompt optimization
- @docs/subagents.md — Dynamic agent factory, templates, spawning
- @docs/hooks.md — Memory tiers, tool integration, audit logging

## Module Index

| Module | Purpose |
|--------|---------|
| `state.py` | `AgentState` TypedDict — the shared whiteboard |
| `agents.py` | Core agent nodes: researcher, writer, reviewer + `get_llm()` |
| `prompts.py` | All system prompts (RESEARCHER, WRITER, REVIEWER, SUPERVISOR, DEBATE_MODERATOR) |
| `memory_layer.py` | 5-tier memory + PatternMemory + TieredRouter + MemoryManager |
| `dynamic_agent_factory.py` | Runtime agent spawning from templates |
| `experiential_learning.py` | Training-Free GRPO (arXiv:2510.08191) |
| `persona_evolution.py` | Search-Synthesise-Compress persona loop |
| `prompt_optimizer.py` | DSPy/GEPA prompt optimization bridge |
| `tool_integration.py` | MCP tool framework with permissions + audit |
