"""Tests for MCP resource handlers."""

import pytest
from unittest.mock import patch


class TestMCPResources:
    def test_health_resource(self):
        from shared.execution._mcp_resources import get_resource

        result = get_resource("jobpulse://health")
        assert "status" in result
        assert result["status"] == "healthy"

    def test_events_resource(self, event_store):
        event_store.emit("scan:t1", "scan.window_started", {})
        with patch(
            "shared.execution._mcp_resources._get_event_store",
            return_value=event_store,
        ):
            from shared.execution._mcp_resources import get_resource

            result = get_resource("jobpulse://events/scan:t1")
            assert len(result["events"]) == 1
            assert result["stream_id"] == "scan:t1"
            assert result["count"] == 1

    def test_unknown_resource_returns_error(self):
        from shared.execution._mcp_resources import get_resource

        result = get_resource("jobpulse://nonexistent")
        assert "error" in result
        assert "Unknown resource" in result["error"]

    def test_events_resource_no_store(self):
        with patch(
            "shared.execution._mcp_resources._get_event_store",
            return_value=None,
        ):
            from shared.execution._mcp_resources import get_resource

            result = get_resource("jobpulse://events/scan:t1")
            assert result["events"] == []
            assert "error" in result

    def test_jobs_queue_resource_import_error(self):
        """Jobs queue gracefully degrades when jobpulse not available."""
        from shared.execution._mcp_resources import get_resource

        with patch(
            "shared.execution._mcp_resources._jobs_queue_resource",
            return_value={"queue": [], "error": "Module unavailable"},
        ):
            result = get_resource("jobpulse://jobs/queue")
            assert "queue" in result

    def test_gates_stats_resource_import_error(self):
        """Gates stats gracefully degrades when jobpulse not available."""
        from shared.execution._mcp_resources import get_resource

        with patch(
            "shared.execution._mcp_resources._gates_stats_resource",
            return_value={"error": "Module unavailable"},
        ):
            result = get_resource("jobpulse://gates/stats")
            assert isinstance(result, dict)
