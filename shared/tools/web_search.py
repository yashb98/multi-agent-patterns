"""Web search tool implementation."""

from shared.safe_fetch import safe_fetch
from shared.tool_integration import ToolDefinition, RiskLevel
from shared.web_search import search_web


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
                    "params": {"query": "str"},
                },
                "fetch_url": {
                    "description": "Fetch the content of a specific URL",
                    "risk": RiskLevel.LOW,
                    "params": {"url": "str"},
                },
            },
            execute_fn=WebSearchTool.execute,
        )

    @staticmethod
    def execute(action: str, params: dict) -> dict:
        if action == "search":
            query = params.get("query", "")
            results = [hit.to_dict() for hit in search_web(query, max_results=5, context="general")]
            return {
                "status": "success",
                "results": results,
            }
        elif action == "fetch_url":
            url = params.get("url", "")

            result = safe_fetch(url)
            return {
                "status": "success",
                "content": result.text,
                "content_type": result.content_type,
                "url": result.url,
            }
        return {"status": "error", "message": f"Unknown action: {action}"}
