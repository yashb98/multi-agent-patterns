from shared.tools.web_search import WebSearchTool
from shared.web_search import WebSearchHit


def test_web_search_tool_returns_structured_results(monkeypatch):
    monkeypatch.setattr(
        "shared.tools.web_search.search_web",
        lambda *args, **kwargs: [
            WebSearchHit(
                title="Example",
                url="https://example.com",
                snippet="hello",
                source="searxng",
            )
        ],
    )

    result = WebSearchTool.execute("search", {"query": "example"})

    assert result["status"] == "success"
    assert result["results"][0]["url"] == "https://example.com"
