"""
Agent Tool Integration Layer — Framework
==========================================

Core framework for tool permission management, audit logging, and execution.
Individual tool implementations live in shared/tools/.

ARCHITECTURE:
    Agent → ToolRegistry → PermissionGate → Tool.execute() → AuditLog

SECURITY MODEL:
- Each agent has an explicit ALLOWLIST of permitted tools
- High-risk actions require human confirmation
- All tool calls are logged with full audit trail
- Rate limiting per agent per tool
"""

import json
from typing import Optional, Callable, Any
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


# ─── PERMISSION LEVELS ──────────────────────────────────────────

class PermissionLevel(Enum):
    DENY = "deny"
    READ_ONLY = "read_only"
    READ_WRITE = "read_write"
    REQUIRES_APPROVAL = "requires_approval"


class RiskLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ─── AUDIT LOG ──────────────────────────────────────────────────

@dataclass
class AuditEntry:
    timestamp: str
    agent_name: str
    tool_name: str
    action: str
    input_summary: str
    output_summary: str
    risk_level: str
    approved_by: str
    success: bool
    error: Optional[str] = None


class AuditLog:
    """Thread-safe audit log for all tool executions."""

    def __init__(self):
        self.entries: list[AuditEntry] = []

    def record(self, entry: AuditEntry):
        self.entries.append(entry)

    def get_recent(self, n: int = 10) -> list[AuditEntry]:
        return self.entries[-n:]

    def get_by_agent(self, agent_name: str) -> list[AuditEntry]:
        return [e for e in self.entries if e.agent_name == agent_name]

    def get_failures(self) -> list[AuditEntry]:
        return [e for e in self.entries if not e.success]

    def summary(self) -> dict:
        total = len(self.entries)
        failures = len(self.get_failures())
        return {
            "total_calls": total,
            "failures": failures,
            "success_rate": (total - failures) / total if total > 0 else 1.0,
            "unique_agents": len(set(e.agent_name for e in self.entries)),
            "unique_tools": len(set(e.tool_name for e in self.entries)),
        }


# ─── TOOL DEFINITIONS ───────────────────────────────────────────

@dataclass
class ToolDefinition:
    name: str
    description: str
    category: str
    actions: dict[str, dict]
    execute_fn: Callable
    requires_api_key: bool = False
    api_key_env_var: str = ""
    rate_limit_per_minute: int = 30


# ─── TOOL REGISTRY ──────────────────────────────────────────────

def _build_all_tools() -> dict[str, ToolDefinition]:
    """Lazy-load tool definitions from shared.tools package."""
    from shared.tools import (
        WebSearchTool, TerminalTool, GmailTool,
        TelegramTool, DiscordTool, LinkedInTool, BrowserTool,
    )
    return {
        "web_search": WebSearchTool.get_definition(),
        "terminal": TerminalTool.get_definition(),
        "gmail": GmailTool.get_definition(),
        "telegram": TelegramTool.get_definition(),
        "discord": DiscordTool.get_definition(),
        "linkedin": LinkedInTool.get_definition(),
        "browser": BrowserTool.get_definition(),
    }


# Default permission profiles per agent role
DEFAULT_PERMISSIONS = {
    "researcher": {
        "web_search": PermissionLevel.READ_ONLY,
        "browser": PermissionLevel.READ_ONLY,
        "terminal": PermissionLevel.DENY,
        "gmail": PermissionLevel.READ_ONLY,
        "telegram": PermissionLevel.DENY,
        "discord": PermissionLevel.READ_ONLY,
        "linkedin": PermissionLevel.DENY,
    },
    "writer": {
        "web_search": PermissionLevel.DENY,
        "browser": PermissionLevel.DENY,
        "terminal": PermissionLevel.DENY,
        "gmail": PermissionLevel.DENY,
        "telegram": PermissionLevel.DENY,
        "discord": PermissionLevel.DENY,
        "linkedin": PermissionLevel.DENY,
    },
    "reviewer": {
        "web_search": PermissionLevel.READ_ONLY,
        "browser": PermissionLevel.DENY,
        "terminal": PermissionLevel.DENY,
        "gmail": PermissionLevel.DENY,
        "telegram": PermissionLevel.DENY,
        "discord": PermissionLevel.DENY,
        "linkedin": PermissionLevel.DENY,
    },
    "code_expert": {
        "web_search": PermissionLevel.READ_ONLY,
        "browser": PermissionLevel.DENY,
        "terminal": PermissionLevel.REQUIRES_APPROVAL,
        "gmail": PermissionLevel.DENY,
        "telegram": PermissionLevel.DENY,
        "discord": PermissionLevel.DENY,
        "linkedin": PermissionLevel.DENY,
    },
    "notifier": {
        "web_search": PermissionLevel.DENY,
        "browser": PermissionLevel.DENY,
        "terminal": PermissionLevel.DENY,
        "gmail": PermissionLevel.READ_WRITE,
        "telegram": PermissionLevel.READ_WRITE,
        "discord": PermissionLevel.READ_WRITE,
        "linkedin": PermissionLevel.REQUIRES_APPROVAL,
    },
}


# ─── TOOL EXECUTOR ──────────────────────────────────────────────

class ToolExecutor:
    """Central hub for all tool execution with permission checking and auditing."""

    def __init__(
        self,
        tools: dict | None = None,
        permissions: dict | None = None,
        approval_fn: Callable | None = None,
    ):
        self.tools = tools if tools is not None else _build_all_tools()
        self.permissions = permissions or DEFAULT_PERMISSIONS
        self.audit = AuditLog()
        self.approval_fn = approval_fn or self._default_approval
        self._call_counts: dict[str, int] = {}

    def execute(
        self,
        agent_name: str,
        tool_name: str,
        action: str,
        params: dict | None = None,
    ) -> dict:
        """Execute a tool action with permission checking and auditing."""
        params = params or {}
        timestamp = datetime.now().strftime("%H:%M:%S")

        # Check tool exists
        tool = self.tools.get(tool_name)
        if not tool:
            self._audit_denied(timestamp, agent_name, tool_name, action, "tool not found")
            return {"status": "error", "message": f"Tool '{tool_name}' not found"}

        # Check action exists
        action_def = tool.actions.get(action)
        if not action_def:
            self._audit_denied(timestamp, agent_name, tool_name, action, "action not found")
            return {"status": "error", "message": f"Action '{action}' not found on tool '{tool_name}'"}

        risk = action_def["risk"]

        # Check permissions
        agent_perms = self.permissions.get(agent_name, {})
        permission = agent_perms.get(tool_name, PermissionLevel.DENY)

        if permission == PermissionLevel.DENY:
            self._audit_denied(timestamp, agent_name, tool_name, action, "permission denied")
            return {"status": "denied", "message": f"Agent '{agent_name}' is not permitted to use '{tool_name}'"}

        if permission == PermissionLevel.READ_ONLY and risk in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            self._audit_denied(timestamp, agent_name, tool_name, action, "read-only, action is write")
            return {"status": "denied", "message": f"Agent '{agent_name}' has read-only access to '{tool_name}'"}

        # Human approval for high-risk or REQUIRES_APPROVAL
        approved_by = "system"
        if permission == PermissionLevel.REQUIRES_APPROVAL or risk == RiskLevel.CRITICAL:
            approved = self.approval_fn(agent_name, tool_name, action, params)
            if not approved:
                self._audit_denied(timestamp, agent_name, tool_name, action, "human rejected")
                return {"status": "denied", "message": "Action rejected by human reviewer"}
            approved_by = "human"

        # Rate limiting
        rate_key = f"{agent_name}:{tool_name}"
        count = self._call_counts.get(rate_key, 0)
        if count >= tool.rate_limit_per_minute:
            self._audit_denied(timestamp, agent_name, tool_name, action, "rate limited")
            return {"status": "rate_limited", "message": "Rate limit exceeded"}
        self._call_counts[rate_key] = count + 1

        # Execute
        try:
            result = tool.execute_fn(action, params)
            success = result.get("status") != "error"

            self.audit.record(AuditEntry(
                timestamp=timestamp, agent_name=agent_name, tool_name=tool_name,
                action=action, input_summary=json.dumps(params)[:200],
                output_summary=json.dumps(result)[:200], risk_level=risk.value,
                approved_by=approved_by, success=success,
            ))
            return result

        except Exception as e:
            self.audit.record(AuditEntry(
                timestamp=timestamp, agent_name=agent_name, tool_name=tool_name,
                action=action, input_summary=json.dumps(params)[:200],
                output_summary="", risk_level=risk.value,
                approved_by=approved_by, success=False, error=str(e),
            ))
            return {"status": "error", "message": str(e)}

    def _audit_denied(self, timestamp: str, agent: str, tool: str, action: str, reason: str):
        self.audit.record(AuditEntry(
            timestamp=timestamp, agent_name=agent, tool_name=tool,
            action=action, input_summary="", output_summary="",
            risk_level="unknown", approved_by="denied",
            success=False, error=reason,
        ))

    @staticmethod
    def _default_approval(agent: str, tool: str, action: str, params: dict) -> bool:
        """Default approval — auto-approves in non-interactive mode."""
        return True

    def get_available_tools(self, agent_name: str) -> list[dict]:
        """List all tools available to a specific agent."""
        agent_perms = self.permissions.get(agent_name, {})
        available = []
        for tool_name, permission in agent_perms.items():
            if permission != PermissionLevel.DENY:
                tool = self.tools.get(tool_name)
                if tool:
                    available.append({
                        "name": tool_name,
                        "description": tool.description,
                        "permission": permission.value,
                        "actions": list(tool.actions.keys()),
                    })
        return available

    def grant_permission(self, agent_name: str, tool_name: str, level: PermissionLevel):
        """Grant or modify tool permissions for an agent."""
        if agent_name not in self.permissions:
            self.permissions[agent_name] = {}
        self.permissions[agent_name][tool_name] = level
