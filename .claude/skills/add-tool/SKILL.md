---
name: add-tool
description: Add a new MCP tool to the tool integration framework
disable-model-invocation: true
---

Add a new tool: $ARGUMENTS

Follow these steps:

1. Read `shared/tool_integration.py` to understand the BaseTool interface and ToolExecutor
2. Create a new tool class that subclasses `BaseTool`:
   - Define `name`, `category`, `default_permission`, `risk_level`
   - Implement `async execute(action: str, params: dict) -> ToolResult`
   - Choose appropriate permission level:
     - `DENY` — blocked by default
     - `READ_ONLY` — can read but not modify
     - `READ_WRITE` — can read and modify
     - `REQUIRES_APPROVAL` — needs human approval each time
   - Choose appropriate risk level: `LOW`, `MEDIUM`, `HIGH`, `CRITICAL`
3. Register the tool with `ToolExecutor.register_tool()`
4. Add any required environment variables to `.env.example`
5. Update `docs/hooks.md` with the new tool's permission and risk info
