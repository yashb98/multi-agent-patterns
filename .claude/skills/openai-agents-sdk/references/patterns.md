# Patterns

## Multi-Agent Pipeline

Sequential stages where output feeds into the next agent:

```python
from pydantic import BaseModel, Field
from agents import Agent, AgentOutputSchema, ModelSettings, RunConfig, Runner

class ProductLite(BaseModel):
    product_id: str
    name: str
    score: float = Field(ge=0, le=1)

class ProductsOutput(BaseModel):
    products: list[ProductLite]

product_selector = Agent(
    name="ProductSelector",
    instructions="Select best products.",
    output_type=AgentOutputSchema(ProductsOutput, strict_json_schema=True),
)

plan_generator = Agent(
    name="PlanGenerator",
    instructions="Generate plan from products.",
)

async def pipeline(user_prompt: str, context: str):
    result = await Runner.run(product_selector, f"User: {user_prompt}\nProducts:\n{context}")
    products = result.final_output.products
    plan_result = await Runner.run(plan_generator, f"Create plan for:\n{products}")
    return plan_result.final_output
```

## LLM as a Judge

```python
from dataclasses import dataclass
from typing import Literal
from agents import Agent, Runner, TResponseInputItem, trace

@dataclass
class Evaluation:
    score: Literal["pass", "needs_improvement", "fail"]
    feedback: str

generator = Agent(name="Generator", instructions="Generate content based on feedback.")
evaluator = Agent(name="Evaluator", instructions="Evaluate and provide feedback.", output_type=Evaluation)

async def generate_with_feedback(prompt: str) -> str:
    inputs: list[TResponseInputItem] = [{"role": "user", "content": prompt}]
    with trace("LLM as a judge"):
        while True:
            gen_result = await Runner.run(generator, inputs)
            inputs = gen_result.to_input_list()
            eval_result = await Runner.run(evaluator, inputs)
            evaluation: Evaluation = eval_result.final_output
            if evaluation.score == "pass":
                return gen_result.final_output
            inputs.append({"role": "user", "content": f"Feedback: {evaluation.feedback}"})
```

## Parallelization

```python
import asyncio
from agents import Agent, Runner

agent1 = Agent(name="Researcher", instructions="Research topics.")
agent2 = Agent(name="Analyzer", instructions="Analyze data.")
agent3 = Agent(name="Writer", instructions="Write content.")

async def parallel_workflow(topic: str):
    research, analysis = await asyncio.gather(
        Runner.run(agent1, f"Research: {topic}"),
        Runner.run(agent2, f"Analyze: {topic}"),
    )
    combined = f"Research: {research.final_output}\nAnalysis: {analysis.final_output}"
    return (await Runner.run(agent3, combined)).final_output
```

## Routing

```python
from agents import Agent, Runner, function_tool
from typing import Literal

@function_tool
def classify_intent(query: str) -> Literal["billing", "technical", "sales"]:
    """Classify user intent."""
    if "invoice" in query or "payment" in query:
        return "billing"
    elif "error" in query or "bug" in query:
        return "technical"
    return "sales"

router = Agent(name="Router", instructions="Classify user intent.", tools=[classify_intent])
agents = {
    "billing": Agent(name="Billing", instructions="Handle billing."),
    "technical": Agent(name="Technical", instructions="Handle tech support."),
    "sales": Agent(name="Sales", instructions="Handle sales."),
}

async def route_and_handle(query: str):
    intent = (await Runner.run(router, query)).final_output
    return (await Runner.run(agents[intent], query)).final_output
```

## Tracing

```python
from agents import Agent, Runner, trace, RunConfig

async def workflow(user_input: str):
    with trace("MyWorkflow"):
        result1 = await Runner.run(agent1, user_input)
        result2 = await Runner.run(agent2, result1.to_input_list())
    return result2.final_output
```
