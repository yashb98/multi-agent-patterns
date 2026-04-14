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
import os
import sqlite3
import time
from pathlib import Path
from typing import Optional, Callable, Any
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from shared.logging_config import get_logger

logger = get_logger(__name__)


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
    """Thread-safe audit log for all tool executions with SQLite persistence."""

    def __init__(self, db_path: str | None = None):
        self.entries: list[AuditEntry] = []
        self._db_path = db_path or str(Path(__file__).parent.parent / "data" / "audit.db")
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT, agent_name TEXT, tool_name TEXT,
            action TEXT, input_summary TEXT, output_summary TEXT,
            risk_level TEXT, approved_by TEXT, success INTEGER,
            error TEXT, created_at TEXT DEFAULT (datetime('now'))
        )""")
        conn.commit()
        conn.close()

    def record(self, entry: AuditEntry):
        self.entries.append(entry)
        conn = sqlite3.connect(self._db_path)
        conn.execute(
            "INSERT INTO audit_log (timestamp, agent_name, tool_name, action, "
            "input_summary, output_summary, risk_level, approved_by, success, error) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (entry.timestamp, entry.agent_name, entry.tool_name, entry.action,
             entry.input_summary, entry.output_summary, entry.risk_level,
             entry.approved_by, entry.success, entry.error),
        )
        conn.commit()
        conn.close()

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
        self._call_timestamps: dict[str, list[float]] = {}

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

        # Rate limiting (sliding window — 60 second window)
        rate_key = f"{agent_name}:{tool_name}"
        now = time.time()
        timestamps = self._call_timestamps.get(rate_key, [])
        timestamps = [t for t in timestamps if now - t < 60]
        if len(timestamps) >= tool.rate_limit_per_minute:
            self._audit_denied(timestamp, agent_name, tool_name, action, "rate limited")
            return {"status": "rate_limited", "message": "Rate limit exceeded"}
        timestamps.append(now)
        self._call_timestamps[rate_key] = timestamps

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
        """Default approval — denies unless TOOL_AUTO_APPROVE=1 is set."""
        if os.environ.get("TOOL_AUTO_APPROVE") == "1":
            return True
        logger.warning(
            "Approval denied for %s/%s/%s — set TOOL_AUTO_APPROVE=1 to auto-approve",
            agent, tool, action,
        )
        return False

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

    def record_dispatch(
        self,
        intent: str,
        agent_name: str,
        result_summary: str,
        success: bool,
        error: str | None = None,
    ) -> None:
        """Record a dispatcher-level agent invocation in the audit log.

        Called by swarm_dispatcher and dispatcher after every agent call,
        even when agents bypass the ToolExecutor to call APIs directly.
        This gives us a unified audit trail for every dispatch event.

        Args:
            intent: The user intent (e.g. "log_spend", "arxiv").
            agent_name: The handler function that ran (e.g. "gmail_agent").
            result_summary: First 200 chars of the result string.
            success: False if the result starts with an error indicator.
            error: Optional error string for failed dispatches.
        """
        self.audit.record(AuditEntry(
            timestamp=datetime.now().strftime("%H:%M:%S"),
            agent_name=agent_name,
            tool_name="dispatch",
            action=intent,
            input_summary=intent,
            output_summary=result_summary[:200],
            risk_level=RiskLevel.LOW.value,
            approved_by="system",
            success=success,
            error=error,
        ))


# ── Shared singleton ────────────────────────────────────────────────────────

_shared_executor: ToolExecutor | None = None


def get_shared_tool_executor() -> ToolExecutor:
    """Return (or create) the shared ToolExecutor singleton.

    The singleton accumulates the full audit log across all dispatches
    in a process lifetime. Call .audit.summary() at any time to see
    total calls, failures, and success rate.
    """
    global _shared_executor
    if _shared_executor is None:
        _shared_executor = ToolExecutor()
    return _shared_executor
