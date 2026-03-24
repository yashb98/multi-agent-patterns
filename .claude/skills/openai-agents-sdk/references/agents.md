# Agents

## Basic Agent

```python
from agents import Agent, Runner

agent = Agent(
    name="Assistant",
    instructions="You are a helpful assistant.",
    model="gpt-5.2",
)

# Synchronous
result = Runner.run_sync(agent, "Tell me a joke")

# Asynchronous
result = await Runner.run(agent, "Tell me a joke")
```

## Dynamic Instructions

```python
from agents import Agent, RunContextWrapper

def personalized_instructions(ctx: RunContextWrapper[dict], agent: Agent) -> str:
    user_name = ctx.context.get("user_name", "User")
    return f"You are helping {user_name}. Be personalized and helpful."

agent = Agent(
    name="PersonalBot",
    instructions=personalized_instructions,
)

result = await Runner.run(agent, "Hello!", context={"user_name": "Yash"})
```

## Loading Prompts from Files

```python
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent / "prompts"

agent = Agent(
    name="Agent",
    instructions=(PROMPTS_DIR / "system_prompt.md").read_text(encoding="utf-8"),
)
```

## Azure / LiteLLM Integration

```python
import os

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai")

def get_model() -> str:
    if LLM_PROVIDER == "azure":
        return "azure/gpt-5.2"  # azure/ prefix tells LiteLLM to use Azure
    return "gpt-5.2"

agent = Agent(
    name="AzureAgent",
    instructions="Be helpful.",
    model=get_model(),
)
```

## Agent Configuration Options

| Option | Description |
|--------|-------------|
| `name` | Agent display name |
| `instructions` | System prompt (str or callable) |
| `model` | Model ID (e.g., "gpt-5.2") |
| `tools` | List of function tools |
| `handoffs` | List of agents to delegate to |
| `output_type` | Pydantic model or AgentOutputSchema |
| `input_guardrails` | Input validation functions |
| `output_guardrails` | Output validation functions |
| `model_settings` | ModelSettings for temperature, etc. |
