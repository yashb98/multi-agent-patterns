# Handoffs

## Basic Handoffs

```python
from agents import Agent, handoff

billing_agent = Agent(name="BillingAgent", instructions="Handle billing questions.")

support_agent = Agent(
    name="SupportAgent",
    instructions="Handle general support. Handoff billing questions to the billing agent.",
    handoffs=[billing_agent],
)

result = await Runner.run(support_agent, "I have a question about my invoice")
```

## Multiple Handoffs

```python
billing_agent = Agent(name="BillingAgent", instructions="Handle billing and payment questions.")
technical_agent = Agent(name="TechnicalAgent", instructions="Handle technical issues.")
sales_agent = Agent(name="SalesAgent", instructions="Handle sales inquiries.")

triage_agent = Agent(
    name="TriageAgent",
    instructions="""Route customers to the appropriate specialist:
    - Billing -> BillingAgent
    - Technical -> TechnicalAgent
    - Sales -> SalesAgent""",
    handoffs=[billing_agent, technical_agent, sales_agent],
)
```

## Handoff with Context

```python
from agents import Agent, handoff, RunContextWrapper

def escalation_instructions(ctx: RunContextWrapper[dict], agent: Agent[dict]) -> str:
    priority = ctx.context.get("priority", "normal")
    return f"You are handling an escalated case. Priority: {priority}"

escalation_agent = Agent(name="EscalationAgent", instructions=escalation_instructions)

support_agent = Agent(
    name="SupportAgent",
    instructions="Handle support. Escalate complex issues.",
    handoffs=[escalation_agent],
)

result = await Runner.run(support_agent, "Urgent help!", context={"priority": "high"})
```

## Handoff vs Agents as Tools

| Feature | Handoffs | Agents as Tools |
|---------|----------|-----------------|
| Control flow | LLM decides when to delegate | Parent agent calls child explicitly |
| Return | Child agent takes over | Returns result to parent |
| Use case | Specialized routing | Orchestration, parallel tasks |
| Conversation | Child continues conversation | Parent continues after tool result |

## Message Filtering

```python
from agents import Agent, handoff, TResponseInputItem

def filter_messages(messages: list[TResponseInputItem]) -> list[TResponseInputItem]:
    return messages[-5:]  # Only keep last 5 messages

specialist = Agent(name="Specialist", instructions="Handle specialized tasks.")

agent = Agent(
    name="Router",
    handoffs=[handoff(agent=specialist, input_filter=filter_messages)],
)
```
