"""Browser automation tool implementation."""

from shared.tool_integration import ToolDefinition, RiskLevel


class BrowserTool:
    """Full browser automation via Playwright."""

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
                    "params": {"url": "str"},
                },
                "screenshot": {
                    "description": "Take a screenshot of the current page",
                    "risk": RiskLevel.LOW,
                    "params": {"url": "str", "output_path": "str"},
                },
                "extract_text": {
                    "description": "Extract all visible text from a page",
                    "risk": RiskLevel.LOW,
                    "params": {"url": "str"},
                },
            },
            execute_fn=BrowserTool.execute,
            rate_limit_per_minute=10,
        )

    @staticmethod
    def execute(action: str, params: dict) -> dict:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return {
                "status": "error",
                "message": "playwright not installed. Run: pip install playwright && playwright install chromium",
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
