"""Briefing agent — collects all agents, synthesizes with RLM, sends to Telegram."""

from shared.logging_config import get_logger

logger = get_logger(__name__)


def fetch_ai_news(max_results: int = 5) -> list[dict]:
    """Fetch top AI news via SearXNG. Returns empty list if SearXNG unavailable."""
    try:
        from shared.searxng_client import search_smart
        results = search_smart(
            "artificial intelligence machine learning news today",
            context="general",
            categories=["news"],
            max_results=max_results,
        )
        return [{"title": r.title, "url": r.url, "summary": r.content[:200]} for r in results]
    except Exception as e:
        logger.debug("AI news fetch failed: %s", e)
        return []
