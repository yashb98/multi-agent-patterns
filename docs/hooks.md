# Hooks

Memory injection, tool integration, audit logging, and lifecycle hooks.

## 1. Memory Injection Hook

**File:** `shared/memory_layer.py`

### Five-Tier Memory Architecture

| Tier | Scope | Dev Storage | Production |
|------|-------|-------------|------------|
| Working | Single execution | In-process AgentState | In-process |
| Short-Term | Current session | `collections.deque` | `collections.deque` |
| Episodic | Cross-run | Local JSON | PostgreSQL |
| Semantic | Cross-run | Local JSON | Qdrant vector DB |
| Procedural | Cross-run | Local JSON | Redis |

### How Memory Is Injected

Agents don't query memory. The `MemoryManager` **pushes** relevant context before each LLM call:

```python
manager = MemoryManager()

# Called before every agent execution:
context = manager.get_context_for_agent(
    agent_name="writer",
    topic="AI Agents",
    domain="technology"
)
# context is merged into the agent's system prompt
```

### Memory Flow

```
Agent about to execute
  → MemoryManager.get_context_for_agent()
    → Retrieve relevant episodic memories (past runs)
    → Retrieve relevant semantic facts (domain knowledge)
    → Retrieve relevant procedural strategies (what worked)
    → Format as prompt context
  → Inject into system prompt
  → Agent executes with enriched context
  → Results stored back to appropriate memory tier
```

## 2. Tool Integration Hook

**File:** `shared/tool_integration.py`

### Execution Pipeline

Every tool action passes through this pipeline:

```
Tool Request
  → Permission Check (DENY/READ_ONLY/READ_WRITE/REQUIRES_APPROVAL)
    → Risk Assessment (LOW/MEDIUM/HIGH/CRITICAL)
      → Human Approval Gate (if HIGH or CRITICAL)
        → Rate Limit Check
          → Execute (sandboxed for code tools)
            → Audit Log Entry
              → Return ToolResult
```

### Available Tools

| Tool | Category | Permission | Risk |
|------|----------|-----------|------|
| `WebSearchTool` | Information | READ_ONLY | LOW |
| `TerminalTool` | Code Execution | READ_WRITE | HIGH |
| `GmailTool` | Communication | REQUIRES_APPROVAL | HIGH |
| `TelegramTool` | Communication | REQUIRES_APPROVAL | MEDIUM |
| `DiscordTool` | Communication | REQUIRES_APPROVAL | MEDIUM |
| `LinkedInTool` | Social Media | REQUIRES_APPROVAL | HIGH |
| `BrowserTool` | Browser | READ_WRITE | MEDIUM |

### Adding a New Tool

1. Subclass `BaseTool` in `tool_integration.py`
2. Define `name`, `category`, `default_permission`, `risk_level`
3. Implement `async execute(action, params) -> ToolResult`
4. Register with `ToolExecutor.register_tool()`

## 3. Audit Logging

**File:** `shared/tool_integration.py` (`AuditLog` class)

Every tool execution creates an immutable audit entry:

- Timestamp
- Tool name and action
- Parameters (sanitized)
- Result status (success/failure)
- Risk level at time of execution
- Whether human approval was required/granted

## 4. Rate Limiting

Tools are rate-limited per category:

- Information tools: High throughput allowed
- Communication tools: Throttled to prevent spam
- Code execution: Throttled with sandboxing
- Browser tools: Throttled for resource management
