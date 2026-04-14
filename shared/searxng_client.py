"""SearXNG client — self-hosted metasearch with optional Tor proxy.

Provides search() for direct queries and search_smart() for auto Tor/no-Tor routing.
Results cached in SQLite with 24h TTL (fast) / 7d TTL (Tor).

Setup:
    docker run -d --name searxng -p 8888:8080 searxng/searxng
    # Optional Tor:
    docker run -d --name searxng-tor -p 8889:8080 searxng/searxng
    docker run -d --name tor-proxy -p 9050:9050 dperson/torproxy
"""

import hashlib
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from shared.logging_config import get_logger
from shared.paths import DATA_DIR as _DATA_DIR

logger = get_logger(__name__)

SEARXNG_URL = os.getenv("SEARXNG_URL", "http://localhost:8888")
SEARXNG_TOR_URL = os.getenv("SEARXNG_TOR_URL", "http://localhost:8889")
CACHE_DB_PATH = _DATA_DIR / "searxng_cache.db"

FAST_TTL_S = 86400      # 24 hours
TOR_TTL_S = 604800      # 7 days

TOR_CONTEXTS = {"salary", "glassdoor", "linkedin_public", "career_page"}


@dataclass
class SearchResult:
    title: str
    url: str
    content: str
    engine: str
    score: float = 0.0

    @classmethod
    def from_dict(cls, d: dict) -> "SearchResult":
        return cls(
            title=d.get("title", ""),
            url=d.get("url", ""),
            content=d.get("content", ""),
            engine=d.get("engine", ""),
            score=d.get("score", 0.0),
        )

    def to_dict(self) -> dict:
        return {"title": self.title, "url": self.url, "content": self.content, "engine": self.engine, "score": self.score}


# ── Cache ──

def _get_cache_db(db_path: Path = None) -> sqlite3.Connection:
    path = db_path or CACHE_DB_PATH
    conn = sqlite3.connect(str(path))
    conn.execute("""CREATE TABLE IF NOT EXISTS searxng_cache (
        cache_key TEXT PRIMARY KEY,
        results_json TEXT NOT NULL,
        created_at REAL NOT NULL
    )""")
    conn.commit()
    return conn


def _cache_key(query: str, engines: list[str], use_tor: bool) -> str:
    raw = f"{query}|{','.join(sorted(engines))}|{use_tor}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _get_cached(key: str, use_tor: bool, db_path: Path = None) -> list[dict] | None:
    ttl = TOR_TTL_S if use_tor else FAST_TTL_S
    try:
        conn = _get_cache_db(db_path)
        row = conn.execute("SELECT results_json, created_at FROM searxng_cache WHERE cache_key=?", (key,)).fetchone()
        conn.close()
        if row and (time.time() - row[1]) < ttl:
            return json.loads(row[0])
    except Exception as e:
        logger.debug("Cache read error: %s", e)
    return None


def _set_cached(key: str, results: list[dict], db_path: Path = None):
    try:
        conn = _get_cache_db(db_path)
        conn.execute(
            "INSERT OR REPLACE INTO searxng_cache (cache_key, results_json, created_at) VALUES (?, ?, ?)",
            (key, json.dumps(results), time.time()),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug("Cache write error: %s", e)


# ── Search ──

def search(
    query: str,
    categories: list[str] | None = None,
    engines: list[str] | None = None,
    max_results: int = 10,
    use_tor: bool = False,
) -> list[SearchResult]:
    """Query local SearXNG instance. Returns structured results.

    Falls back to empty list on connection error (SearXNG not running).
    """
    engines_list = engines or []
    key = _cache_key(query, engines_list, use_tor)

    # Check cache
    cached = _get_cached(key, use_tor)
    if cached is not None:
        logger.debug("SearXNG cache hit for: %s", query[:60])
        return [SearchResult.from_dict(r) for r in cached[:max_results]]

    base_url = os.getenv("SEARXNG_TOR_URL", SEARXNG_TOR_URL) if use_tor else os.getenv("SEARXNG_URL", SEARXNG_URL)
    params = {
        "q": query,
        "format": "json",
    }
    if categories:
        params["categories"] = ",".join(categories)
    if engines_list:
        params["engines"] = ",".join(engines_list)

    try:
        resp = httpx.get(f"{base_url}/search", params=params, timeout=15 if not use_tor else 30)
        if resp.status_code != 200:
            logger.warning("SearXNG returned %d for: %s", resp.status_code, query[:60])
            return []

        raw_results = resp.json().get("results", [])[:max_results]
        results = [SearchResult.from_dict(r) for r in raw_results]

        # Cache
        _set_cached(key, [r.to_dict() for r in results])

        logger.info("SearXNG: %d results for '%s' (tor=%s)", len(results), query[:40], use_tor)
        return results

    except httpx.ConnectError:
        logger.debug("SearXNG not reachable at %s", base_url)
        return []
    except Exception as e:
        logger.warning("SearXNG search failed: %s", e)
        return []


def search_smart(query: str, context: str = "general", **kwargs) -> list[SearchResult]:
    """Auto-select SearXNG mode based on context.

    Tor contexts (slow, anonymous): salary, glassdoor, linkedin_public, career_page
    Fast contexts (everything else): general, company, interview, news, fact_check
    """
    use_tor = context in TOR_CONTEXTS
    return search(query, use_tor=use_tor, **kwargs)
