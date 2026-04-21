"""JobPulse MCP Capability Server — exposes job automation tools.

Each tool wraps an existing function from jobpulse/. No new business logic.
"""

from __future__ import annotations

from shared.execution._mcp_gateway import CapabilityServer
from shared.logging_config import get_logger

logger = get_logger(__name__)


def _job_stats_handler(params: dict) -> dict:
    try:
        from jobpulse.job_analytics import get_conversion_funnel, get_platform_breakdown

        period = params.get("period", "week")
        return {
            "funnel": get_conversion_funnel(period),
            "platforms": get_platform_breakdown(period),
        }
    except Exception as e:
        return {"error": str(e)}


def _pre_screen_handler(params: dict) -> dict:
    try:
        from jobpulse.jd_analyzer import analyze_jd

        url = params.get("url", "")
        result = analyze_jd(url)
        return {"analysis": result}
    except Exception as e:
        return {"error": str(e)}


def _budget_handler(params: dict) -> dict:
    try:
        from jobpulse.budget_agent import handle_budget

        command = params.get("command", "")
        return {"response": handle_budget(command)}
    except Exception as e:
        return {"error": str(e)}


def _morning_briefing_handler(params: dict) -> dict:
    try:
        from jobpulse.briefing_agent import generate_briefing

        return {"briefing": generate_briefing()}
    except Exception as e:
        return {"error": str(e)}


def create_jobpulse_server() -> CapabilityServer:
    """Create a JobPulse capability server with 4 tools."""
    server = CapabilityServer(namespace="jobpulse")
    server.register_tool(
        "job_stats",
        _job_stats_handler,
        "Job application conversion funnel and platform breakdown",
    )
    server.register_tool(
        "pre_screen",
        _pre_screen_handler,
        "Pre-screen a job listing URL through Gates 0-3",
    )
    server.register_tool(
        "budget",
        _budget_handler,
        "Budget query, add transaction, or undo",
    )
    server.register_tool(
        "morning_briefing",
        _morning_briefing_handler,
        "Generate morning briefing digest",
    )
    return server
