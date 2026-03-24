# Guardrails

## Input Guardrails

```python
from agents import Agent, Runner, input_guardrail
from agents import GuardrailFunctionOutput, RunContextWrapper

@input_guardrail
async def check_appropriate(ctx: RunContextWrapper, agent: Agent, input: str) -> GuardrailFunctionOutput:
    is_inappropriate = "bad_word" in input.lower()
    return GuardrailFunctionOutput(
        tripwire_triggered=is_inappropriate,
        output_info="Inappropriate content detected" if is_inappropriate else None,
    )

@input_guardrail
async def check_length(ctx: RunContextWrapper, agent: Agent, input: str) -> GuardrailFunctionOutput:
    if len(input) > 10000:
        return GuardrailFunctionOutput(tripwire_triggered=True, output_info="Input too long")
    return GuardrailFunctionOutput(tripwire_triggered=False)

agent = Agent(name="SafeAgent", instructions="Be helpful.", input_guardrails=[check_appropriate, check_length])
```

## Output Guardrails

```python
from agents import Agent, output_guardrail
from agents import GuardrailFunctionOutput, RunContextWrapper
import re

@output_guardrail
async def check_no_pii(ctx: RunContextWrapper, agent: Agent, output: str) -> GuardrailFunctionOutput:
    has_email = bool(re.search(r'\b[\w.-]+@[\w.-]+\.\w+\b', output))
    has_phone = bool(re.search(r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b', output))
    if has_email or has_phone:
        return GuardrailFunctionOutput(tripwire_triggered=True, output_info="Output contains PII")
    return GuardrailFunctionOutput(tripwire_triggered=False)

agent = Agent(name="PIISafeAgent", instructions="Help users.", output_guardrails=[check_no_pii])
```

## Guardrail with Context

```python
@input_guardrail
async def check_user_permissions(ctx: RunContextWrapper[dict], agent: Agent, input: str) -> GuardrailFunctionOutput:
    user_role = ctx.context.get("user_role", "guest")
    if "admin" in input.lower() and user_role != "admin":
        return GuardrailFunctionOutput(tripwire_triggered=True, output_info="Admin access not permitted")
    return GuardrailFunctionOutput(tripwire_triggered=False)

result = await Runner.run(agent, "Show admin settings", context={"user_role": "user"})
```

## Handling Guardrail Errors

```python
from agents import InputGuardrailTripwireTriggered

try:
    result = await Runner.run(agent, "Some bad_word input")
except InputGuardrailTripwireTriggered as e:
    print(f"Input blocked: {e.guardrail_result.output_info}")
```

## GuardrailFunctionOutput Fields

| Field | Description |
|-------|-------------|
| `tripwire_triggered` | True if guardrail should block |
| `output_info` | Human-readable explanation |
