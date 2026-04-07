"""Agentic Loop — shared error type and tool registry.

Provides AgentError (structured error response), AGENT_TOOLS dict,
and register_agent_tool() used across all agent systems.
"""

from typing import Any

from shared.logging_config import get_logger

logger = get_logger(__name__)


# ─── STRUCTURED ERROR RESPONSE ───────────────────────────────────

class AgentError:
    """Structured error object for agent failures."""

    def __init__(self, error_category: str, message: str,
                 is_retryable: bool = False, partial_results: Any = None,
                 agent_name: str = "", attempted_action: str = ""):
        self.error_category = error_category   # transient | validation | permission | business
        self.message = message
        self.is_retryable = is_retryable
        self.partial_results = partial_results
        self.agent_name = agent_name
        self.attempted_action = attempted_action

    def to_dict(self) -> dict:
        return {
            "status": "error",
            "errorCategory": self.error_category,
            "message": self.message,
            "isRetryable": self.is_retryable,
            "partialResults": self.partial_results,
            "agentName": self.agent_name,
            "attemptedAction": self.attempted_action,
        }

    def __str__(self) -> str:
        retry = " (retryable)" if self.is_retryable else ""
        return f"[{self.error_category}]{retry} {self.agent_name}: {self.message}"


# ─── TOOL REGISTRY ───────────────────────────────────────────────

AGENT_TOOLS = {}


def register_agent_tool(name: str, description: str, func: callable):
    """Register a tool that agents can invoke during agentic loops."""
    AGENT_TOOLS[name] = {
        "name": name,
        "description": description,
        "func": func,
    }
