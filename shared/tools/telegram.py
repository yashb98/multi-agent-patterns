"""Telegram tool implementation."""

import os
import urllib.parse

from shared.telegram_client import telegram_url
from shared.tool_integration import ToolDefinition, RiskLevel


class TelegramTool:
    """Telegram Bot API integration."""

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
                    "params": {"chat_id": "str", "text": "str"},
                },
                "get_updates": {
                    "description": "Get recent messages",
                    "risk": RiskLevel.LOW,
                    "params": {"limit": "int"},
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

        if action == "send_message":
            chat_id = params.get("chat_id", "")
            text = params.get("text", "")
            query = urllib.parse.urlencode({"chat_id": chat_id, "text": text})
            url = f"{telegram_url(token, 'sendMessage')}?{query}"
            try:
                with urllib.request.urlopen(url, timeout=10) as resp:
                    return {"status": "success", "response": resp.read().decode()[:500]}
            except Exception as e:
                return {"status": "error", "message": str(e)}

        elif action == "get_updates":
            limit = params.get("limit", 10)
            query = urllib.parse.urlencode({"limit": limit})
            url = f"{telegram_url(token, 'getUpdates')}?{query}"
            try:
                with urllib.request.urlopen(url, timeout=10) as resp:
                    return {"status": "success", "updates": resp.read().decode()[:2000]}
            except Exception as e:
                return {"status": "error", "message": str(e)}

        return {"status": "error", "message": f"Unknown action: {action}"}
