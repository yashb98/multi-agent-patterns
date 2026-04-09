"""Discord tool implementation."""

import json
import os

from shared.tool_integration import ToolDefinition, RiskLevel


class DiscordTool:
    """Discord Bot integration."""

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
                    "params": {"channel_id": "str", "content": "str"},
                },
                "read_channel": {
                    "description": "Read recent messages from a channel",
                    "risk": RiskLevel.LOW,
                    "params": {"channel_id": "str", "limit": "int"},
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
