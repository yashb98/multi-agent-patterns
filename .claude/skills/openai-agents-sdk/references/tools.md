# Tools

## Function Tools (@function_tool)

```python
from typing import Annotated
from agents import Agent, Runner, function_tool

@function_tool
def get_weather(city: Annotated[str, "City name"]) -> str:
    """Get weather for a city."""
    return f"Weather in {city}: Sunny, 20C"

@function_tool
async def search_database(query: Annotated[str, "Search query"]) -> list[dict]:
    """Search products in database."""
    return [{"id": "1", "name": "Hiking boots"}]

agent = Agent(
    name="Assistant",
    instructions="Help users find information.",
    tools=[get_weather, search_database],
)
```

## Hosted Tools (Built-in)

```python
from agents import Agent, WebSearchTool, CodeInterpreterTool

agent = Agent(
    name="Researcher",
    instructions="Search the web and analyze data.",
    tools=[
        WebSearchTool(user_location={"type": "approximate", "city": "Helsinki"}),
        CodeInterpreterTool(),
    ],
)
```

## Agents as Tools

```python
translator_es = Agent(name="SpanishTranslator", instructions="Translate to Spanish.")
translator_fr = Agent(name="FrenchTranslator", instructions="Translate to French.")

orchestrator = Agent(
    name="Orchestrator",
    instructions="Use translation tools as needed.",
    tools=[
        translator_es.as_tool(tool_name="translate_spanish", tool_description="Translate to Spanish"),
        translator_fr.as_tool(tool_name="translate_french", tool_description="Translate to French"),
    ],
)
```

## Tool Guardrails

```python
from agents import Agent, function_tool, tool_guardrail
from agents import ToolGuardrailFunctionOutput, RunContextWrapper

@tool_guardrail
async def validate_query(ctx: RunContextWrapper, agent: Agent, tool_input: dict) -> ToolGuardrailFunctionOutput:
    query = tool_input.get("query", "")
    if len(query) < 3:
        return ToolGuardrailFunctionOutput(tripwire_triggered=True, output_info="Query too short")
    return ToolGuardrailFunctionOutput(tripwire_triggered=False)

@function_tool(guardrails=[validate_query])
def search(query: Annotated[str, "Search query"]) -> list[str]:
    """Search for items."""
    return ["result1", "result2"]
```

## Forcing Tool Use

```python
from agents import Agent, ModelSettings

agent = Agent(
    name="ToolUser",
    instructions="Always use tools to answer.",
    tools=[get_weather, search_database],
    model_settings=ModelSettings(tool_choice="required"),
)
```
