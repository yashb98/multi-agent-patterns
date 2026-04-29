"""Shared web search helpers for agent and tool use.

Search strategy:
1. Prefer the local SearXNG client when available.
2. Fall back to DuckDuckGo HTML results when SearXNG is unavailable.

This keeps the `web_search` tool usable in environments without the local
metasearch service while still preferring the faster, more controllable path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable
from urllib.parse import parse_qs, quote_plus, urlparse

from bs4 import BeautifulSoup

from shared.logging_config import get_logger
from shared.safe_fetch import safe_fetch_text
from shared.searxng_client import search_smart

logger = get_logger(__name__)


@dataclass(frozen=True)
class WebSearchHit:
    title: str
    url: str
    snippet: str
    source: str

    def to_dict(self) -> dict[str, str]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "source": self.source,
        }


def _dedupe_hits(hits: Iterable[WebSearchHit], max_results: int) -> list[WebSearchHit]:
    deduped: list[WebSearchHit] = []
    seen: set[str] = set()
    for hit in hits:
        key = hit.url.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(hit)
        if len(deduped) >= max_results:
            break
    return deduped


def _normalise_duckduckgo_href(href: str) -> str:
    if not href:
        return ""
    parsed = urlparse(href)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        return target or href
    return href


def _search_with_searxng(query: str, max_results: int, context: str) -> list[WebSearchHit]:
    try:
        results = search_smart(query, context=context, max_results=max_results)
    except TypeError:
        # Older call sites may not accept max_results/context together.
        results = search_smart(query, context=context)[:max_results]
    return _dedupe_hits(
        [
            WebSearchHit(
                title=result.title,
                url=result.url,
                snippet=result.content,
                source=result.engine or "searxng",
            )
            for result in results
            if result.url
        ],
        max_results=max_results,
    )


def _search_duckduckgo_html(query: str, max_results: int) -> list[WebSearchHit]:
    html = safe_fetch_text(
        f"https://html.duckduckgo.com/html/?q={quote_plus(query)}&kl=uk-en",
        timeout=15,
        max_bytes=1_500_000,
        follow_redirects=True,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
        },
    )
    soup = BeautifulSoup(html, "html.parser")
    hits: list[WebSearchHit] = []
    for block in soup.select(".result"):
        link = block.select_one("a.result__a")
        if link is None:
            continue
        title = link.get_text(" ", strip=True)
        url = _normalise_duckduckgo_href(link.get("href", ""))
        snippet_el = block.select_one(".result__snippet")
        snippet = snippet_el.get_text(" ", strip=True) if snippet_el is not None else ""
        hits.append(
            WebSearchHit(
                title=title,
                url=url,
                snippet=snippet,
                source="duckduckgo_html",
            )
        )
    return _dedupe_hits(hits, max_results=max_results)


def search_web(
    query: str,
    *,
    max_results: int = 5,
    context: str = "general",
) -> list[WebSearchHit]:
    """Return structured web search results with safe public-web fallback."""
    searxng_hits = _search_with_searxng(query, max_results=max_results, context=context)
    if searxng_hits:
        return searxng_hits

    try:
        return _search_duckduckgo_html(query, max_results=max_results)
    except Exception as exc:
        logger.warning("DuckDuckGo fallback search failed: %s", exc)
        return []
