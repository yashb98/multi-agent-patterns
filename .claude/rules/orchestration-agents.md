---
paths: ["shared/agents.py", "patterns/**/*.py", "shared/dynamic_agent_factory.py"]
description: "Agentic loop and orchestration conventions"
---

# Orchestration Agent Conventions

## Agentic Loop Pattern

All agent loops MUST use stop_reason-based control flow:

```python
while iteration < max_iterations:
    response = client.chat.completions.create(...)
    if choice.finish_reason == "tool_calls":
        # Execute tools, append results to messages, continue
    elif choice.finish_reason in ("stop", "end_turn"):
        # Return final content — model decided to stop
        break
```

## Anti-Patterns (NEVER do these)

- Parsing natural language to determine loop termination
- Using iteration caps as the PRIMARY stopping mechanism (they are safety valves only)
- Checking for assistant text content as a completion indicator
- Skipping tool result injection — tool results MUST be appended to conversation history

## Score-Based Convergence

- Primary stop: `review_score >= 7.0` (model-driven)
- Secondary stop: no score improvement across rounds (patience counter)
- Safety valve: `iteration < 3` (prevents infinite loops, NOT the decision driver)

## Structured Output

- Use `response_format={"type": "json_object"}` when expecting JSON from OpenAI
- Define clear JSON schemas in prompts for machine-readable output
- Never rely on markdown stripping to extract JSON from responses
