# Structured Output

## Pydantic Output Schema

```python
from pydantic import BaseModel, Field
from agents import Agent, Runner, AgentOutputSchema, ModelSettings

class ProductRecommendation(BaseModel):
    product_id: str = Field(description="Unique product ID")
    name: str = Field(description="Product name")
    relevance_reason: str = Field(description="Why this product matches")
    match_score: float = Field(ge=0, le=1, description="Match score 0-1")

class ProductSelectionOutput(BaseModel):
    products: list[ProductRecommendation] = Field(description="Selected products")

agent = Agent(
    name="ProductSelector",
    instructions="Select best products matching user request.",
    output_type=AgentOutputSchema(ProductSelectionOutput, strict_json_schema=True),
)

result = await Runner.run(agent, "Find products for hiking trip")
output: ProductSelectionOutput = result.final_output
for product in output.products:
    print(f"{product.name}: {product.match_score}")
```

## Simple Output Type (Dataclass)

```python
from dataclasses import dataclass
from typing import Literal

@dataclass
class EvaluationFeedback:
    feedback: str
    score: Literal["pass", "needs_improvement", "fail"]

evaluator = Agent[None](
    name="Evaluator",
    instructions="Evaluate content and provide feedback.",
    output_type=EvaluationFeedback,
)

result = await Runner.run(evaluator, "Review this outline...")
evaluation: EvaluationFeedback = result.final_output
```

## ModelSettings

```python
from agents import Agent, ModelSettings
from openai.types.shared.reasoning import Reasoning

agent = Agent(
    name="Assistant",
    model="gpt-5.2",
    model_settings=ModelSettings(
        max_tokens=32000,
        temperature=0.7,
        tool_choice="required",
        reasoning=Reasoning(effort="medium"),
    ),
)
```

| Option | Description |
|--------|-------------|
| `max_tokens` | Maximum tokens in response |
| `temperature` | Randomness (0.0-2.0) |
| `top_p` | Nucleus sampling |
| `tool_choice` | "auto", "required", "none" |
| `reasoning` | Reasoning effort for GPT-5 models |

## Non-Strict Output

```python
class FlexibleOutput(BaseModel):
    data: dict  # dict type not supported in strict mode
    notes: str

agent = Agent(
    name="Flexible",
    output_type=AgentOutputSchema(FlexibleOutput, strict_json_schema=False),
)
```
