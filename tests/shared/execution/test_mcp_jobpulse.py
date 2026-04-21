"""Tests for JobPulse MCP Capability Server."""

import pytest
from unittest.mock import patch, MagicMock


class TestJobPulseCapabilityServer:
    def test_registers_tools(self):
        from shared.execution._mcp_jobpulse import create_jobpulse_server

        server = create_jobpulse_server()
        tools = server.list_tools()
        tool_names = [t["name"] for t in tools]
        assert "jobpulse.job_stats" in tool_names
        assert "jobpulse.pre_screen" in tool_names
        assert "jobpulse.budget" in tool_names
        assert "jobpulse.morning_briefing" in tool_names

    @pytest.mark.asyncio
    async def test_job_stats_tool(self):
        from shared.execution._mcp_jobpulse import create_jobpulse_server

        server = create_jobpulse_server()
        with patch("shared.execution._mcp_jobpulse._job_stats_handler") as mock:
            mock.return_value = {"funnel": {}, "platforms": {}}
            # Re-register with the mock so the server uses it
            server._tools["job_stats"]["handler"] = mock
            result = await server.call_tool("job_stats", {"period": "week"})
            assert "funnel" in result
            mock.assert_called_once_with({"period": "week"})

    @pytest.mark.asyncio
    async def test_unknown_tool_raises(self):
        from shared.execution._mcp_jobpulse import create_jobpulse_server

        server = create_jobpulse_server()
        with pytest.raises(KeyError):
            await server.call_tool("nonexistent", {})
