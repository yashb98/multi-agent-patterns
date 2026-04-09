"""LinkedIn tool implementation."""

import os

from shared.tool_integration import ToolDefinition, RiskLevel


class LinkedInTool:
    """LinkedIn API integration for posting content."""

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
                    "params": {"text": "str"},
                },
                "get_profile": {
                    "description": "Get your LinkedIn profile info",
                    "risk": RiskLevel.LOW,
                    "params": {},
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
                "note": "Implement with LinkedIn API v2 /ugcPosts endpoint",
            }
        elif action == "get_profile":
            return {
                "status": "success",
                "note": "Implement with LinkedIn API /me endpoint",
            }
        return {"status": "error", "message": f"Unknown action: {action}"}
