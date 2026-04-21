"""Tests for MCP Gateway — FastAPI router that multiplexes capability servers."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def gateway_app():
    from shared.execution._mcp_gateway import (
        create_gateway_app,
        _capability_servers,
        register_capability_server,
        CapabilityServer,
    )
    # Clear global state before each test
    _capability_servers.clear()
    app = create_gateway_app()
    yield app
    _capability_servers.clear()


@pytest.fixture
def client(gateway_app):
    return TestClient(gateway_app)


@pytest.fixture
def demo_server():
    """Register a demo capability server with one sync and one async tool."""
    from shared.execution._mcp_gateway import (
        CapabilityServer,
        register_capability_server,
    )

    server = CapabilityServer(namespace="demo")
    server.register_tool("echo", lambda params: {"echoed": params}, description="Echo params back")

    async def async_upper(params):
        return {"result": params.get("text", "").upper()}

    server.register_tool("upper", async_upper, description="Uppercase text")
    register_capability_server(server)
    return server


class TestMCPGateway:
    def test_health_endpoint(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert data["status"] in ("healthy", "degraded")

    def test_list_tools(self, client):
        resp = client.get("/mcp/tools")
        assert resp.status_code == 200
        tools = resp.json()["tools"]
        assert isinstance(tools, list)

    def test_list_tools_empty_when_no_servers(self, client):
        resp = client.get("/mcp/tools")
        assert resp.json()["tools"] == []

    def test_list_tools_with_server(self, client, demo_server):
        resp = client.get("/mcp/tools")
        tools = resp.json()["tools"]
        assert len(tools) == 2
        names = {t["name"] for t in tools}
        assert "demo.echo" in names
        assert "demo.upper" in names

    def test_call_unknown_tool_returns_404(self, client):
        resp = client.post("/mcp/call", json={"tool": "nonexistent.tool", "params": {}})
        assert resp.status_code == 404

    def test_call_malformed_tool_name_returns_404(self, client):
        resp = client.post("/mcp/call", json={"tool": "no_dot_here", "params": {}})
        assert resp.status_code == 404

    def test_call_unknown_namespace_returns_404(self, client, demo_server):
        resp = client.post("/mcp/call", json={"tool": "unknown.echo", "params": {}})
        assert resp.status_code == 404

    def test_call_unknown_tool_in_known_namespace_returns_404(self, client, demo_server):
        resp = client.post("/mcp/call", json={"tool": "demo.missing", "params": {}})
        assert resp.status_code == 404

    def test_call_sync_tool(self, client, demo_server):
        resp = client.post("/mcp/call", json={"tool": "demo.echo", "params": {"msg": "hello"}})
        assert resp.status_code == 200
        assert resp.json()["result"] == {"echoed": {"msg": "hello"}}

    def test_call_async_tool(self, client, demo_server):
        resp = client.post("/mcp/call", json={"tool": "demo.upper", "params": {"text": "hello"}})
        assert resp.status_code == 200
        assert resp.json()["result"] == {"result": "HELLO"}

    def test_audit_log_emits_event(self, client, demo_server, event_store):
        with pytest.MonkeyPatch.context() as m:
            m.setattr("shared.execution._mcp_gateway._get_event_store", lambda: event_store)
            resp = client.post("/mcp/call", json={"tool": "demo.echo", "params": {}})
            assert resp.status_code == 200
            # Verify audit event was emitted
            events = event_store.get_stream("mcp:audit")
            assert len(events) == 1
            assert events[0]["event_type"] == "mcp.tool_called"
            assert events[0]["payload"]["tool"] == "demo.echo"
            assert events[0]["payload"]["success"] is True


class TestCapabilityServer:
    def test_register_and_list_tools(self):
        from shared.execution._mcp_gateway import CapabilityServer

        server = CapabilityServer(namespace="test")
        server.register_tool("greet", lambda p: {"hi": True}, description="Say hi")
        tools = server.list_tools()
        assert len(tools) == 1
        assert tools[0]["name"] == "test.greet"
        assert tools[0]["description"] == "Say hi"

    @pytest.mark.asyncio
    async def test_call_sync_tool(self):
        from shared.execution._mcp_gateway import CapabilityServer

        server = CapabilityServer(namespace="test")
        server.register_tool("add", lambda p: {"sum": p["a"] + p["b"]})
        result = await server.call_tool("add", {"a": 1, "b": 2})
        assert result == {"sum": 3}

    @pytest.mark.asyncio
    async def test_call_async_tool(self):
        from shared.execution._mcp_gateway import CapabilityServer

        async def multiply(params):
            return {"product": params["a"] * params["b"]}

        server = CapabilityServer(namespace="test")
        server.register_tool("mul", multiply)
        result = await server.call_tool("mul", {"a": 3, "b": 4})
        assert result == {"product": 12}

    @pytest.mark.asyncio
    async def test_call_unknown_tool_raises(self):
        from shared.execution._mcp_gateway import CapabilityServer

        server = CapabilityServer(namespace="test")
        with pytest.raises(KeyError, match="Tool nope not found in test"):
            await server.call_tool("nope", {})
