from shared.web_search import WebSearchHit, search_web


def test_search_web_falls_back_to_duckduckgo(monkeypatch):
    sample_html = """
    <html>
      <div class="result">
        <a class="result__a" href="https://example.com/post">Example result</a>
        <div class="result__snippet">Snippet text</div>
      </div>
    </html>
    """

    monkeypatch.setattr("shared.web_search.search_smart", lambda *args, **kwargs: [])
    monkeypatch.setattr("shared.web_search.safe_fetch_text", lambda *args, **kwargs: sample_html)

    results = search_web("example query", max_results=5)

    assert results == [
        WebSearchHit(
            title="Example result",
            url="https://example.com/post",
            snippet="Snippet text",
            source="duckduckgo_html",
        )
    ]
