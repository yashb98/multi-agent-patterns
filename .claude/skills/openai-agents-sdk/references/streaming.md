# Streaming

## Basic Streaming

```python
from openai.types.responses import ResponseTextDeltaEvent
from agents import Agent, Runner

agent = Agent(name="Writer", instructions="Write stories.")

result = Runner.run_streamed(agent, input="Write a short story")

async for event in result.stream_events():
    if event.type == "raw_response_event":
        if isinstance(event.data, ResponseTextDeltaEvent):
            print(event.data.delta, end="", flush=True)
```

## SSE Streaming with FastAPI

```python
import json
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from openai.types.responses import ResponseTextDeltaEvent
from agents import Agent, Runner

app = FastAPI()
agent = Agent(name="Assistant", instructions="Be helpful.")

def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"

@app.post("/stream")
async def stream_response(prompt: str):
    async def generate():
        result = Runner.run_streamed(agent, input=prompt)
        async for event in result.stream_events():
            if event.type == "raw_response_event":
                if isinstance(event.data, ResponseTextDeltaEvent):
                    yield sse("delta", {"text": event.data.delta})
        yield sse("done", {})
    return StreamingResponse(generate(), media_type="text/event-stream")
```

## Streaming with Tool Calls

```python
from openai.types.responses import ResponseTextDeltaEvent, ResponseFunctionCallArgumentsDeltaEvent

result = Runner.run_streamed(agent, input="Get data about sales")

async for event in result.stream_events():
    if event.type == "raw_response_event":
        if isinstance(event.data, ResponseTextDeltaEvent):
            print(f"Text: {event.data.delta}", end="")
        elif isinstance(event.data, ResponseFunctionCallArgumentsDeltaEvent):
            print(f"Tool args: {event.data.delta}", end="")
```

## Collecting Full Response After Streaming

```python
result = Runner.run_streamed(agent, input="Tell me a story")

async for event in result.stream_events():
    if event.type == "raw_response_event":
        if isinstance(event.data, ResponseTextDeltaEvent):
            print(event.data.delta, end="")

final_result = await result.final_result()
print(f"\nFull output: {final_result.final_output}")
```
