"""MCP Gateway — multiplexes capability servers behind one HTTP endpoint.

Thin router with audit logging and health checks.
Zero business logic — delegates to capability servers.
"""

from __future__ import annotations

import asyncio
from typing import Callable

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from shared.logging_config import get_logger

logger = get_logger(__name__)

_capability_servers: dict[str, CapabilityServer] = {}


class ToolCallRequest(BaseModel):
    tool: str
    params: dict = {}


class CapabilityServer:
    """Base class for MCP capability servers."""

    def __init__(self, namespace: str):
        self.namespace = namespace
        self._tools: dict[str, dict] = {}

    def register_tool(self, name: str, handler: Callable, description: str = "") -> None:
        self._tools[name] = {"handler": handler, "description": description}

    def list_tools(self) -> list[dict]:
        return [
            {"name": f"{self.namespace}.{name}", "description": t["description"]}
            for name, t in self._tools.items()
        ]

    async def call_tool(self, name: str, params: dict) -> dict:
        if name not in self._tools:
            raise KeyError(f"Tool {name} not found in {self.namespace}")
        handler = self._tools[name]["handler"]
        if asyncio.iscoroutinefunction(handler):
            return await handler(params)
        return handler(params)


def register_capability_server(server: CapabilityServer) -> None:
    """Register a capability server by its namespace."""
    _capability_servers[server.namespace] = server


def _get_event_store():
    """Get the shared event store for audit logging. Returns None if unavailable."""
    try:
        from shared.execution import get_event_store
        return get_event_store()
    except Exception:
        return None


def create_gateway_app() -> FastAPI:
    """Create the MCP Gateway FastAPI application."""
    app = FastAPI(title="MCP Gateway", version="1.0.0")

    from shared.governance._api_auth import require_auth
    require_auth(app)

    @app.get("/health")
    def health():
        servers_status = {
            ns: "healthy" for ns in _capability_servers
        }
        overall = (
            "healthy"
            if all(s == "healthy" for s in servers_status.values())
            else "degraded"
        )
        return {"status": overall, "servers": servers_status}

    @app.get("/mcp/tools")
    def list_tools():
        all_tools = []
        for server in _capability_servers.values():
            all_tools.extend(server.list_tools())
        return {"tools": all_tools}

    @app.post("/mcp/call")
    async def call_tool(req: ToolCallRequest):
        parts = req.tool.split(".", 1)
        if len(parts) != 2:
            raise HTTPException(404, f"Tool must be namespace.name, got: {req.tool}")
        namespace, name = parts
        if namespace not in _capability_servers:
            raise HTTPException(404, f"Unknown namespace: {namespace}")
        try:
            result = await _capability_servers[namespace].call_tool(name, req.params)
            store = _get_event_store()
            if store:
                store.emit("mcp:audit", "mcp.tool_called", {
                    "tool": req.tool,
                    "success": True,
                })
            return {"result": result}
        except KeyError:
            raise HTTPException(404, f"Unknown tool: {req.tool}")
        except Exception as e:
            logger.error("MCP tool call failed: %s — %s", req.tool, e)
            raise HTTPException(500, str(e))

    return app
