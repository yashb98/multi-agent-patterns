# Subagents

Dynamic agent factory, templates, runtime spawning, and custom agent creation.

**File:** `shared/dynamic_agent_factory.py`

## Overview

Instead of pre-registering all agents, the factory analyzes task complexity and spawns the right agents at runtime. This is used by the Enhanced Swarm pattern.

```
Task → TaskComplexityAnalyzer → complexity_score + required_capabilities
                                         │
                               DynamicAgentFactory
                                         │
                   ┌─────────────────────┼─────────────────────┐
                   ▼                     ▼                     ▼
             Researcher           Code Expert           Custom Agent
          (from template)      (from template)       (LLM-invented)
```

## Key Classes

### TaskComplexityAnalyzer
- Analyzes task requirements and outputs a complexity score (0.0-1.0)
- Identifies required capabilities (e.g., "code analysis", "data interpretation")
- Used to determine how many and which agents to spawn

### DynamicAgentFactory
- Spawns agents from templates based on complexity analysis
- Enforces max 7 concurrent agents (prevents coordination overhead)
- Manages complexity budget allocation

### AgentTemplate
- Blueprint for dynamically spawnable agents
- Contains: name, capability list, base prompt, domain customization function

## Built-in Templates

| Template | Capability | When Spawned |
|----------|-----------|--------------|
| `researcher` | Information gathering | Always (core) |
| `writer` | Content creation | Always (core) |
| `reviewer` | Quality assessment | Always (core) |
| `code_expert` | Code analysis/generation | Task involves code |
| `fact_checker` | Claim verification | High-stakes content |
| `seo_optimizer` | SEO optimization | Marketing content |
| `data_analyst` | Data interpretation | Tasks with data |
| `audience_adapter` | Audience targeting | Broad audience content |

## Custom Agent Creation

When no template fits the task, the factory uses an LLM to invent a new agent type:

```python
factory = DynamicAgentFactory()
custom = factory.create_custom_agent(
    task_description="Translate technical concepts for a 10-year-old",
    required_capabilities=["simplification", "analogies"]
)
```

The LLM generates:
- Agent name and role description
- Specialized system prompt
- Capability mapping
- Output format specification

## Complexity Budget

The factory enforces a complexity budget to prevent over-spawning:

- Simple tasks (score < 0.3): 2-3 agents (core only)
- Medium tasks (0.3-0.7): 3-5 agents (core + specialists)
- Complex tasks (score > 0.7): 5-7 agents (core + specialists + custom)

## Adding a New Template

1. Create an `AgentTemplate` with name, capabilities, and base prompt
2. Register with `DynamicAgentFactory.register_template()`
3. The factory will automatically consider it during spawning decisions
