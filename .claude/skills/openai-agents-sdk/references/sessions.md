# Sessions

## Manual History with to_input_list()

```python
from agents import Agent, Runner, TResponseInputItem

agent = Agent(name="ChatBot", instructions="Be helpful.")

result = await Runner.run(agent, "Hello!")
inputs = result.to_input_list()
inputs.append({"role": "user", "content": "Tell me more"})
result = await Runner.run(agent, inputs)
```

## SQLite Session

```python
from agents import Agent, Runner
from agents.extensions.sessions import SQLiteSession

agent = Agent(name="ChatBot", instructions="Remember our conversation.")
session = SQLiteSession("conversation_123")

result1 = await Runner.run(agent, "My name is John", session=session)
result2 = await Runner.run(agent, "What's my name?", session=session)
# -> "Your name is John"
```

## Redis Session (Distributed)

```python
from agents.extensions.sessions import RedisSession

session = RedisSession(session_id="user_789", redis_url="redis://localhost:6379", ttl=3600)
result = await Runner.run(agent, "Hello!", session=session)
```

## Compaction Session (Long Conversations)

```python
from agents.extensions.sessions import CompactionSession, SQLiteSession

base_session = SQLiteSession("long_conversation")
session = CompactionSession(
    base_session=base_session,
    max_messages=20,
    summary_model="gpt-5.2-mini",
)
```

## Encrypted Session

```python
from agents.extensions.sessions import EncryptedSession, SQLiteSession

session = EncryptedSession(
    base_session=SQLiteSession("sensitive_chat"),
    encryption_key="your-32-byte-encryption-key-here",
)
```

## Session Comparison

| Session Type | Storage | Use Case |
|--------------|---------|----------|
| Manual (to_input_list) | Memory | Simple, single-request |
| SQLiteSession | Local file | Single-server apps |
| RedisSession | Redis | Distributed systems |
| OpenAISession | OpenAI | Using OpenAI memory |
| CompactionSession | Wrapper | Long conversations |
| EncryptedSession | Wrapper | Sensitive data |
