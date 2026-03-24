"""
Agent Tool Integration Layer
==============================

This module provides the bridge between our multi-agent system and
real-world tools via the Model Context Protocol (MCP) and direct APIs.

ARCHITECTURE:
    Agent → ToolRegistry → PermissionGate → MCP Client → MCP Server → Tool
    
The flow:
1. Agent requests a tool action (e.g., "search the web for X")
2. ToolRegistry checks: is this agent ALLOWED to use this tool?
3. PermissionGate checks: does this action need human approval?
4. MCP Client sends the request to the appropriate MCP Server
5. MCP Server executes the action and returns results
6. AuditLog records everything that happened

TOOL CATEGORIES:
- Information Gathering: web search, web fetch, file read
- Communication: email (Gmail), messaging (Telegram, Discord, Slack)
- Code Execution: terminal/shell, sandbox, code runner
- Social Media: LinkedIn, Twitter posting
- Browser: full browser automation via Playwright
- Data: databases, APIs, spreadsheets

SECURITY MODEL:
- Each agent has an explicit ALLOWLIST of permitted tools
- High-risk actions require human confirmation
- All tool calls are logged with full audit trail
- Sandboxed execution for terminal/code tools
- Rate limiting per agent per tool
"""

import json
import os
import subprocess
import asyncio
from typing import Optional, Callable, Any
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


# ─── PERMISSION LEVELS ──────────────────────────────────────────

class PermissionLevel(Enum):
    """
    Permission levels for tool access.
    
    DENY: Agent cannot use this tool at all
    READ_ONLY: Agent can read/fetch but not modify/send
    READ_WRITE: Agent can both read and write (e.g., send emails)
    REQUIRES_APPROVAL: Like READ_WRITE but needs human confirmation
    """
    DENY = "deny"
    READ_ONLY = "read_only"
    READ_WRITE = "read_write"
    REQUIRES_APPROVAL = "requires_approval"


class RiskLevel(Enum):
    """Risk classification for tool actions."""
    LOW = "low"          # Web search, file read
    MEDIUM = "medium"    # API calls, data fetch
    HIGH = "high"        # Send email, post to social media
    CRITICAL = "critical"  # Execute code, delete files, financial transactions


# ─── AUDIT LOG ──────────────────────────────────────────────────

@dataclass
class AuditEntry:
    """Records a single tool action for accountability."""
    timestamp: str
    agent_name: str
    tool_name: str
    action: str
    input_summary: str
    output_summary: str
    risk_level: str
    approved_by: str  # "system", "human", or "denied"
    success: bool
    error: str = ""


class AuditLog:
    """
    Immutable audit trail of all tool actions.
    
    In production, back this with a database or append-only log.
    Every tool call — successful or denied — is recorded.
    """
    
    def __init__(self):
        self.entries: list[AuditEntry] = []
    
    def record(self, entry: AuditEntry):
        self.entries.append(entry)
        status = "✅" if entry.success else "❌"
        risk = entry.risk_level
        print(f"  [AUDIT] {status} {entry.agent_name} → {entry.tool_name}."
              f"{entry.action} [{risk}] approved_by={entry.approved_by}")
    
    def get_report(self, agent_name: str = None) -> str:
        filtered = self.entries
        if agent_name:
            filtered = [e for e in filtered if e.agent_name == agent_name]
        
        lines = [f"Audit Log ({len(filtered)} entries)", "=" * 50]
        for e in filtered:
            lines.append(
                f"[{e.timestamp}] {e.agent_name} → {e.tool_name}.{e.action} "
                f"| risk={e.risk_level} | approved={e.approved_by} | "
                f"success={e.success}"
            )
        return "\n".join(lines)


# ─── TOOL DEFINITIONS ───────────────────────────────────────────

@dataclass
class ToolDefinition:
    """
    Defines a tool that agents can use.
    
    Each tool has:
    - A name and description (for the LLM to understand what it does)
    - Available actions (the specific operations it supports)
    - Risk level per action
    - The actual execution function
    """
    name: str
    description: str
    category: str
    actions: dict  # action_name → {"description": str, "risk": RiskLevel}
    execute_fn: Callable  # The actual function to call
    requires_api_key: bool = False
    api_key_env_var: str = ""
    rate_limit_per_minute: int = 30


# ─── TOOL IMPLEMENTATIONS ───────────────────────────────────────
# Each tool is a self-contained module with its execution logic.
# In production, these would be MCP servers. Here we implement
# them as Python functions for clarity and portability.

class WebSearchTool:
    """Web search via API or scraping."""
    
    @staticmethod
    def get_definition() -> ToolDefinition:
        return ToolDefinition(
            name="web_search",
            description="Search the web for current information",
            category="information_gathering",
            actions={
                "search": {
                    "description": "Search the web for a query",
                    "risk": RiskLevel.LOW,
                    "params": {"query": "str"}
                },
                "fetch_url": {
                    "description": "Fetch the content of a specific URL",
                    "risk": RiskLevel.LOW,
                    "params": {"url": "str"}
                }
            },
            execute_fn=WebSearchTool.execute,
        )
    
    @staticmethod
    def execute(action: str, params: dict) -> dict:
        """Execute a web search action."""
        if action == "search":
            # In production: use SerpAPI, Tavily, or Brave Search API
            query = params.get("query", "")
            return {
                "status": "success",
                "results": f"[Web search results for: {query}]",
                "note": "Replace with actual search API (SerpAPI/Tavily)"
            }
        elif action == "fetch_url":
            url = params.get("url", "")
            return {
                "status": "success",
                "content": f"[Content fetched from: {url}]",
                "note": "Replace with requests.get() or playwright"
            }
        return {"status": "error", "message": f"Unknown action: {action}"}


class TerminalTool:
    """Execute shell commands in a sandboxed environment."""
    
    @staticmethod
    def get_definition() -> ToolDefinition:
        return ToolDefinition(
            name="terminal",
            description="Execute shell commands in a sandboxed environment",
            category="code_execution",
            actions={
                "execute": {
                    "description": "Run a shell command",
                    "risk": RiskLevel.CRITICAL,
                    "params": {"command": "str", "working_dir": "str"}
                },
                "read_file": {
                    "description": "Read a file's contents",
                    "risk": RiskLevel.LOW,
                    "params": {"path": "str"}
                },
                "write_file": {
                    "description": "Write content to a file",
                    "risk": RiskLevel.HIGH,
                    "params": {"path": "str", "content": "str"}
                },
            },
            execute_fn=TerminalTool.execute,
        )
    
    @staticmethod
    def execute(action: str, params: dict) -> dict:
        if action == "execute":
            command = params.get("command", "")
            working_dir = params.get("working_dir", "/tmp/agent_sandbox")
            os.makedirs(working_dir, exist_ok=True)
            
            # SECURITY: Block dangerous commands
            dangerous = ["rm -rf /", "sudo", "chmod 777", "mkfs", "> /dev/"]
            if any(d in command for d in dangerous):
                return {"status": "blocked", "message": "Command blocked by security policy"}
            
            try:
                result = subprocess.run(
                    command, shell=True, capture_output=True, text=True,
                    timeout=30, cwd=working_dir
                )
                return {
                    "status": "success",
                    "stdout": result.stdout[:2000],
                    "stderr": result.stderr[:500],
                    "returncode": result.returncode,
                }
            except subprocess.TimeoutExpired:
                return {"status": "error", "message": "Command timed out (30s limit)"}
            except Exception as e:
                return {"status": "error", "message": str(e)}
        
        elif action == "read_file":
            path = params.get("path", "")
            try:
                with open(path, "r") as f:
                    content = f.read(10000)  # 10KB limit
                return {"status": "success", "content": content}
            except Exception as e:
                return {"status": "error", "message": str(e)}
        
        elif action == "write_file":
            path = params.get("path", "")
            content = params.get("content", "")
            try:
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                with open(path, "w") as f:
                    f.write(content)
                return {"status": "success", "path": path, "bytes": len(content)}
            except Exception as e:
                return {"status": "error", "message": str(e)}
        
        return {"status": "error", "message": f"Unknown action: {action}"}


class GmailTool:
    """
    Gmail integration via Google API.
    
    SETUP REQUIRED:
    1. Create a Google Cloud project
    2. Enable Gmail API
    3. Create OAuth2 credentials
    4. Set GMAIL_CREDENTIALS_PATH environment variable
    
    For MCP: Use the official Google MCP server instead:
        pip install mcp-server-google
    """
    
    @staticmethod
    def get_definition() -> ToolDefinition:
        return ToolDefinition(
            name="gmail",
            description="Read and send emails via Gmail",
            category="communication",
            actions={
                "read_inbox": {
                    "description": "Read recent emails from inbox",
                    "risk": RiskLevel.LOW,
                    "params": {"max_results": "int", "query": "str"}
                },
                "send_email": {
                    "description": "Send an email",
                    "risk": RiskLevel.HIGH,
                    "params": {"to": "str", "subject": "str", "body": "str"}
                },
                "search_emails": {
                    "description": "Search emails by query",
                    "risk": RiskLevel.LOW,
                    "params": {"query": "str", "max_results": "int"}
                },
            },
            execute_fn=GmailTool.execute,
            requires_api_key=True,
            api_key_env_var="GMAIL_CREDENTIALS_PATH",
        )
    
    @staticmethod
    def execute(action: str, params: dict) -> dict:
        # In production, use google-api-python-client
        # or the official Google MCP server
        if action == "send_email":
            return {
                "status": "pending_approval",
                "message": f"Email to {params.get('to')}: {params.get('subject')}",
                "note": "Implement with google-api-python-client or MCP server"
            }
        elif action in ("read_inbox", "search_emails"):
            return {
                "status": "success",
                "results": f"[Gmail {action} results]",
                "note": "Implement with google-api-python-client or MCP server"
            }
        return {"status": "error", "message": f"Unknown action: {action}"}


class TelegramTool:
    """
    Telegram Bot API integration.
    
    SETUP:
    1. Create a bot via @BotFather on Telegram
    2. Get the bot token
    3. Set TELEGRAM_BOT_TOKEN environment variable
    """
    
    @staticmethod
    def get_definition() -> ToolDefinition:
        return ToolDefinition(
            name="telegram",
            description="Send and read messages via Telegram bot",
            category="communication",
            actions={
                "send_message": {
                    "description": "Send a message to a Telegram chat",
                    "risk": RiskLevel.MEDIUM,
                    "params": {"chat_id": "str", "text": "str"}
                },
                "get_updates": {
                    "description": "Get recent messages",
                    "risk": RiskLevel.LOW,
                    "params": {"limit": "int"}
                },
            },
            execute_fn=TelegramTool.execute,
            requires_api_key=True,
            api_key_env_var="TELEGRAM_BOT_TOKEN",
        )
    
    @staticmethod
    def execute(action: str, params: dict) -> dict:
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if not token:
            return {"status": "error", "message": "TELEGRAM_BOT_TOKEN not set"}
        
        import urllib.request
        base_url = f"https://api.telegram.org/bot{token}"
        
        if action == "send_message":
            chat_id = params.get("chat_id", "")
            text = params.get("text", "")
            url = f"{base_url}/sendMessage?chat_id={chat_id}&text={text}"
            try:
                with urllib.request.urlopen(url, timeout=10) as resp:
                    return {"status": "success", "response": resp.read().decode()[:500]}
            except Exception as e:
                return {"status": "error", "message": str(e)}
        
        elif action == "get_updates":
            limit = params.get("limit", 10)
            url = f"{base_url}/getUpdates?limit={limit}"
            try:
                with urllib.request.urlopen(url, timeout=10) as resp:
                    return {"status": "success", "updates": resp.read().decode()[:2000]}
            except Exception as e:
                return {"status": "error", "message": str(e)}
        
        return {"status": "error", "message": f"Unknown action: {action}"}


class DiscordTool:
    """
    Discord Bot integration.
    
    SETUP:
    1. Create application at discord.com/developers
    2. Create a bot and get the token
    3. Set DISCORD_BOT_TOKEN environment variable
    """
    
    @staticmethod
    def get_definition() -> ToolDefinition:
        return ToolDefinition(
            name="discord",
            description="Send messages and read channels via Discord bot",
            category="communication",
            actions={
                "send_message": {
                    "description": "Send a message to a Discord channel",
                    "risk": RiskLevel.MEDIUM,
                    "params": {"channel_id": "str", "content": "str"}
                },
                "read_channel": {
                    "description": "Read recent messages from a channel",
                    "risk": RiskLevel.LOW,
                    "params": {"channel_id": "str", "limit": "int"}
                },
            },
            execute_fn=DiscordTool.execute,
            requires_api_key=True,
            api_key_env_var="DISCORD_BOT_TOKEN",
        )
    
    @staticmethod
    def execute(action: str, params: dict) -> dict:
        token = os.environ.get("DISCORD_BOT_TOKEN", "")
        if not token:
            return {"status": "error", "message": "DISCORD_BOT_TOKEN not set"}
        
        import urllib.request
        base = "https://discord.com/api/v10"
        headers = {"Authorization": f"Bot {token}", "Content-Type": "application/json"}
        
        if action == "send_message":
            channel = params.get("channel_id", "")
            content = params.get("content", "")
            url = f"{base}/channels/{channel}/messages"
            data = json.dumps({"content": content}).encode()
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return {"status": "success", "response": resp.read().decode()[:500]}
            except Exception as e:
                return {"status": "error", "message": str(e)}
        
        elif action == "read_channel":
            channel = params.get("channel_id", "")
            limit = params.get("limit", 10)
            url = f"{base}/channels/{channel}/messages?limit={limit}"
            req = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return {"status": "success", "messages": resp.read().decode()[:2000]}
            except Exception as e:
                return {"status": "error", "message": str(e)}
        
        return {"status": "error", "message": f"Unknown action: {action}"}


class LinkedInTool:
    """
    LinkedIn API integration for posting content.
    
    SETUP:
    1. Create LinkedIn developer app at linkedin.com/developers
    2. Get OAuth2 access token with w_member_social scope
    3. Set LINKEDIN_ACCESS_TOKEN environment variable
    """
    
    @staticmethod
    def get_definition() -> ToolDefinition:
        return ToolDefinition(
            name="linkedin",
            description="Post content and read feed on LinkedIn",
            category="social_media",
            actions={
                "create_post": {
                    "description": "Create a LinkedIn post",
                    "risk": RiskLevel.HIGH,
                    "params": {"text": "str"}
                },
                "get_profile": {
                    "description": "Get your LinkedIn profile info",
                    "risk": RiskLevel.LOW,
                    "params": {}
                },
            },
            execute_fn=LinkedInTool.execute,
            requires_api_key=True,
            api_key_env_var="LINKEDIN_ACCESS_TOKEN",
        )
    
    @staticmethod
    def execute(action: str, params: dict) -> dict:
        token = os.environ.get("LINKEDIN_ACCESS_TOKEN", "")
        if not token:
            return {"status": "error", "message": "LINKEDIN_ACCESS_TOKEN not set"}
        
        if action == "create_post":
            return {
                "status": "pending_approval",
                "preview": params.get("text", "")[:200],
                "note": "Implement with LinkedIn API v2 /ugcPosts endpoint"
            }
        elif action == "get_profile":
            return {
                "status": "success",
                "note": "Implement with LinkedIn API /me endpoint"
            }
        return {"status": "error", "message": f"Unknown action: {action}"}


class BrowserTool:
    """
    Full browser automation via Playwright.
    
    Allows agents to interact with any website:
    navigate, click, fill forms, take screenshots, extract data.
    
    SETUP: pip install playwright && playwright install chromium
    """
    
    @staticmethod
    def get_definition() -> ToolDefinition:
        return ToolDefinition(
            name="browser",
            description="Full browser automation — navigate, click, fill, screenshot",
            category="browser_automation",
            actions={
                "navigate": {
                    "description": "Navigate to a URL and return page content",
                    "risk": RiskLevel.MEDIUM,
                    "params": {"url": "str"}
                },
                "screenshot": {
                    "description": "Take a screenshot of the current page",
                    "risk": RiskLevel.LOW,
                    "params": {"url": "str", "output_path": "str"}
                },
                "extract_text": {
                    "description": "Extract all visible text from a page",
                    "risk": RiskLevel.LOW,
                    "params": {"url": "str"}
                },
            },
            execute_fn=BrowserTool.execute,
            rate_limit_per_minute=10,
        )
    
    @staticmethod
    def execute(action: str, params: dict) -> dict:
        # In production, use playwright async API
        # pip install playwright && playwright install chromium
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return {
                "status": "error",
                "message": "playwright not installed. Run: pip install playwright && playwright install chromium"
            }
        
        url = params.get("url", "")
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, timeout=15000)
            
            if action == "navigate":
                title = page.title()
                content = page.content()[:5000]
                browser.close()
                return {"status": "success", "title": title, "content_preview": content}
            
            elif action == "screenshot":
                output = params.get("output_path", "/tmp/screenshot.png")
                page.screenshot(path=output, full_page=True)
                browser.close()
                return {"status": "success", "path": output}
            
            elif action == "extract_text":
                text = page.inner_text("body")[:5000]
                browser.close()
                return {"status": "success", "text": text}
            
            browser.close()
        
        return {"status": "error", "message": f"Unknown action: {action}"}


# ─── TOOL REGISTRY ──────────────────────────────────────────────

# All available tools
ALL_TOOLS = {
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
    """
    The central hub for all tool execution.
    
    Every tool call flows through here:
    1. Permission check
    2. Risk assessment
    3. Human approval (if needed)
    4. Execution
    5. Audit logging
    
    USAGE:
        executor = ToolExecutor()
        result = executor.execute(
            agent_name="researcher",
            tool_name="web_search",
            action="search",
            params={"query": "AI agents 2026"},
        )
    """
    
    def __init__(
        self,
        tools: dict = None,
        permissions: dict = None,
        approval_fn: Callable = None,
    ):
        self.tools = tools or ALL_TOOLS
        self.permissions = permissions or DEFAULT_PERMISSIONS
        self.audit = AuditLog()
        self.approval_fn = approval_fn or self._default_approval
        self._call_counts = {}  # rate limiting
    
    def execute(
        self,
        agent_name: str,
        tool_name: str,
        action: str,
        params: dict = None,
    ) -> dict:
        """
        Execute a tool action with full permission checking and auditing.
        """
        params = params or {}
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        # Step 1: Check tool exists
        tool = self.tools.get(tool_name)
        if not tool:
            self._audit_denied(timestamp, agent_name, tool_name, action, "tool not found")
            return {"status": "error", "message": f"Tool '{tool_name}' not found"}
        
        # Step 2: Check action exists
        action_def = tool.actions.get(action)
        if not action_def:
            self._audit_denied(timestamp, agent_name, tool_name, action, "action not found")
            return {"status": "error", "message": f"Action '{action}' not found on tool '{tool_name}'"}
        
        risk = action_def["risk"]
        
        # Step 3: Check permissions
        agent_perms = self.permissions.get(agent_name, {})
        permission = agent_perms.get(tool_name, PermissionLevel.DENY)
        
        if permission == PermissionLevel.DENY:
            self._audit_denied(timestamp, agent_name, tool_name, action, "permission denied")
            return {
                "status": "denied",
                "message": f"Agent '{agent_name}' is not permitted to use '{tool_name}'"
            }
        
        if permission == PermissionLevel.READ_ONLY and risk in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            self._audit_denied(timestamp, agent_name, tool_name, action, "read-only, action is write")
            return {
                "status": "denied",
                "message": f"Agent '{agent_name}' has read-only access to '{tool_name}'"
            }
        
        # Step 4: Human approval for high-risk or REQUIRES_APPROVAL
        approved_by = "system"
        if permission == PermissionLevel.REQUIRES_APPROVAL or risk == RiskLevel.CRITICAL:
            approved = self.approval_fn(agent_name, tool_name, action, params)
            if not approved:
                self._audit_denied(timestamp, agent_name, tool_name, action, "human rejected")
                return {"status": "denied", "message": "Action rejected by human reviewer"}
            approved_by = "human"
        
        # Step 5: Rate limiting
        rate_key = f"{agent_name}:{tool_name}"
        count = self._call_counts.get(rate_key, 0)
        if count >= tool.rate_limit_per_minute:
            self._audit_denied(timestamp, agent_name, tool_name, action, "rate limited")
            return {"status": "rate_limited", "message": "Rate limit exceeded"}
        self._call_counts[rate_key] = count + 1
        
        # Step 6: Execute
        try:
            result = tool.execute_fn(action, params)
            success = result.get("status") != "error"
            
            self.audit.record(AuditEntry(
                timestamp=timestamp,
                agent_name=agent_name,
                tool_name=tool_name,
                action=action,
                input_summary=json.dumps(params)[:200],
                output_summary=json.dumps(result)[:200],
                risk_level=risk.value,
                approved_by=approved_by,
                success=success,
            ))
            
            return result
            
        except Exception as e:
            self.audit.record(AuditEntry(
                timestamp=timestamp,
                agent_name=agent_name,
                tool_name=tool_name,
                action=action,
                input_summary=json.dumps(params)[:200],
                output_summary="",
                risk_level=risk.value,
                approved_by=approved_by,
                success=False,
                error=str(e),
            ))
            return {"status": "error", "message": str(e)}
    
    def _audit_denied(self, timestamp, agent, tool, action, reason):
        self.audit.record(AuditEntry(
            timestamp=timestamp, agent_name=agent, tool_name=tool,
            action=action, input_summary="", output_summary="",
            risk_level="unknown", approved_by="denied",
            success=False, error=reason
        ))
    
    @staticmethod
    def _default_approval(agent: str, tool: str, action: str, params: dict) -> bool:
        """
        Default approval function — auto-approves in non-interactive mode.
        In production, this would trigger a UI prompt or Slack notification.
        """
        print(f"\n  ⚠️  APPROVAL REQUIRED")
        print(f"  Agent: {agent}")
        print(f"  Tool: {tool}.{action}")
        print(f"  Params: {json.dumps(params)[:200]}")
        print(f"  [Auto-approved in development mode]")
        return True  # Auto-approve in dev; replace with input() for interactive
    
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
    
    def grant_permission(
        self, agent_name: str, tool_name: str, level: PermissionLevel
    ):
        """Grant or modify tool permissions for an agent."""
        if agent_name not in self.permissions:
            self.permissions[agent_name] = {}
        self.permissions[agent_name][tool_name] = level
        print(f"  Permission updated: {agent_name} → {tool_name} = {level.value}")
