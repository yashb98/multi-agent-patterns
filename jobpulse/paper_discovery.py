"""Community-first paper discovery — find papers people are talking about.

Replaces the old 200-paper arXiv API fetch with community-driven discovery
from 5 sources: HuggingFace, Reddit, Hacker News, Papers with Code, X/Twitter.
"""
import re
import os
import time
import sqlite3
import httpx
from datetime import datetime, timezone, timedelta
from pathlib import Path
from shared.logging_config import get_logger
from jobpulse.config import DATA_DIR

logger = get_logger(__name__)


def _get_email() -> str:
    try:
        from shared.profile_store import get_profile_store
        return get_profile_store().identity().email or "noreply@jobpulse.dev"
    except Exception:
        return "noreply@jobpulse.dev"


NITTER_INSTANCES = [
    "https://nitter.net",
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
    "https://nitter.woodland.cafe",
]

ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5})")


def dedup_by_arxiv_id(papers: list[dict]) -> list[dict]:
    """Deduplicate papers by arXiv ID, aggregating community_buzz."""
    seen: dict[str, dict] = {}
    for p in papers:
        aid = p.get("arxiv_id", "")
        if not aid:
            continue
        if aid in seen:
            seen[aid]["community_buzz"] = seen[aid].get("community_buzz", 0) + p.get("community_buzz", 0)
            sources = seen[aid].get("sources", [seen[aid].get("source", "")])
            sources.append(p.get("source", ""))
            seen[aid]["sources"] = sources
        else:
            seen[aid] = dict(p)
            seen[aid]["sources"] = [p.get("source", "")]
    return list(seen.values())


class NitterHealthTracker:
    """Track Nitter instance health, learn block patterns, auto-adapt."""

    def __init__(self, db_path: Path = None):
        self.db_path = db_path or DATA_DIR / "nitter_health.db"
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS nitter_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instance TEXT NOT NULL,
                success INTEGER NOT NULL,
                response_code INTEGER,
                latency_ms INTEGER,
                timestamp TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS nitter_cooldowns (
                instance TEXT PRIMARY KEY,
                cooldown_until TEXT NOT NULL,
                consecutive_failures INTEGER DEFAULT 0
            )
        """)
        conn.commit()
        conn.close()

    def record_attempt(self, instance: str, success: bool,
                       response_code: int = 0, latency_ms: int = 0):
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "INSERT INTO nitter_attempts (instance, success, response_code, latency_ms, timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            (instance, int(success), response_code, latency_ms,
             datetime.now(timezone.utc).isoformat()),
        )
        if success:
            conn.execute("DELETE FROM nitter_cooldowns WHERE instance = ?", (instance,))
        else:
            row = conn.execute(
                "SELECT consecutive_failures FROM nitter_cooldowns WHERE instance = ?",
                (instance,),
            ).fetchone()
            failures = (row[0] + 1) if row else 1
            hours = min(24, 2 * (2 ** (failures - 1)))
            cooldown_until = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
            conn.execute(
                "INSERT OR REPLACE INTO nitter_cooldowns (instance, cooldown_until, consecutive_failures) "
                "VALUES (?, ?, ?)",
                (instance, cooldown_until, failures),
            )
        conn.commit()
        conn.close()

    def get_success_rate(self, instance: str, window_hours: int = 24) -> float:
        conn = sqlite3.connect(str(self.db_path))
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
        row = conn.execute(
            "SELECT COUNT(*), SUM(success) FROM nitter_attempts "
            "WHERE instance = ? AND timestamp > ?",
            (instance, cutoff),
        ).fetchone()
        conn.close()
        total, successes = row[0] or 0, row[1] or 0
        return successes / total if total > 0 else 0.5

    def get_best_instance(self) -> str:
        now = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(str(self.db_path))
        cooled = {
            row[0]
            for row in conn.execute(
                "SELECT instance FROM nitter_cooldowns WHERE cooldown_until > ?",
                (now,),
            ).fetchall()
        }
        conn.close()
        available = [i for i in NITTER_INSTANCES if i not in cooled]
        if not available:
            return NITTER_INSTANCES[0]
        return max(available, key=lambda i: self.get_success_rate(i))

    def should_skip_x(self) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(str(self.db_path))
        cooled_count = conn.execute(
            "SELECT COUNT(*) FROM nitter_cooldowns WHERE cooldown_until > ?",
            (now,),
        ).fetchone()[0]
        conn.close()
        return cooled_count >= len(NITTER_INSTANCES)


def _extract_arxiv_ids(text: str) -> list[str]:
    return ARXIV_ID_RE.findall(text)


def _get_env(key: str, default: str) -> str:
    return os.getenv(key, default)


def fetch_huggingface_daily() -> list[dict]:
    try:
        resp = httpx.get("https://huggingface.co/api/daily_papers", timeout=15)
        resp.raise_for_status()
        papers = []
        for item in resp.json():
            paper = item.get("paper", {})
            arxiv_id = paper.get("id", "")
            if not arxiv_id:
                continue
            papers.append({
                "arxiv_id": arxiv_id,
                "title": paper.get("title", ""),
                "source": "huggingface",
                "community_buzz": item.get("numUpvotes", 0),
            })
        logger.info("HuggingFace: %d papers", len(papers))
        return papers
    except Exception as e:
        logger.warning("HuggingFace fetch failed: %s", e)
        return []


def fetch_reddit_papers() -> list[dict]:
    try:
        import praw
        reddit = praw.Reddit(
            client_id=_get_env("REDDIT_CLIENT_ID", ""),
            client_secret=_get_env("REDDIT_CLIENT_SECRET", ""),
            user_agent="JobPulse/1.0 paper-discovery",
        )
        papers = []
        for sub_name in ["MachineLearning", "LocalLLaMA"]:
            sub = reddit.subreddit(sub_name)
            for post in sub.new(limit=50):
                if time.time() - post.created_utc > 86400:
                    continue
                ids = _extract_arxiv_ids(post.url + " " + post.selftext)
                for aid in ids:
                    papers.append({
                        "arxiv_id": aid,
                        "title": post.title,
                        "source": "reddit",
                        "community_buzz": post.score,
                    })
        logger.info("Reddit: %d papers", len(papers))
        return papers
    except Exception as e:
        logger.warning("Reddit fetch failed: %s", e)
        return []


def fetch_hackernews_papers() -> list[dict]:
    try:
        resp = httpx.get(
            "https://hn.algolia.com/api/v1/search_by_date",
            params={"query": "arxiv.org", "tags": "story", "numericFilters": "created_at_i>%d" % (time.time() - 86400)},
            timeout=15,
        )
        resp.raise_for_status()
        papers = []
        for hit in resp.json().get("hits", []):
            url = hit.get("url", "")
            ids = _extract_arxiv_ids(url + " " + hit.get("title", ""))
            for aid in ids:
                papers.append({
                    "arxiv_id": aid,
                    "title": hit.get("title", ""),
                    "source": "hackernews",
                    "community_buzz": hit.get("points", 0),
                })
        logger.info("HackerNews: %d papers", len(papers))
        return papers
    except Exception as e:
        logger.warning("HackerNews fetch failed: %s", e)
        return []


def fetch_papers_with_code() -> list[dict]:
    try:
        resp = httpx.get("https://paperswithcode.com/api/v1/papers/", params={"ordering": "-proceeding"}, timeout=15)
        resp.raise_for_status()
        papers = []
        for item in resp.json().get("results", [])[:20]:
            arxiv_id = item.get("arxiv_id", "")
            if not arxiv_id:
                url = item.get("url_abs", "")
                ids = _extract_arxiv_ids(url)
                arxiv_id = ids[0] if ids else ""
            if arxiv_id:
                papers.append({
                    "arxiv_id": arxiv_id,
                    "title": item.get("title", ""),
                    "source": "paperswithcode",
                    "community_buzz": item.get("stars", 0),
                })
        logger.info("PapersWithCode: %d papers", len(papers))
        return papers
    except Exception as e:
        logger.warning("PapersWithCode fetch failed: %s", e)
        return []


def fetch_x_via_searxng(nitter_tracker: NitterHealthTracker = None) -> list[dict]:
    searxng_url = os.getenv("SEARXNG_URL", "http://localhost:8888")
    tracker = nitter_tracker or NitterHealthTracker()
    if tracker.should_skip_x():
        logger.info("X/Nitter: all instances blocked, skipping")
        return []
    try:
        resp = httpx.get(
            f"{searxng_url}/search",
            params={"q": "arxiv paper", "engines": "nitter", "format": "json", "time_range": "day"},
            timeout=15,
        )
        if resp.status_code != 200:
            tracker.record_attempt(searxng_url, success=False, response_code=resp.status_code)
            return []
        tracker.record_attempt(searxng_url, success=True, response_code=200, latency_ms=int(resp.elapsed.total_seconds() * 1000))
        papers = []
        for result in resp.json().get("results", []):
            content = result.get("content", "") + " " + result.get("url", "")
            ids = _extract_arxiv_ids(content)
            for aid in ids:
                papers.append({
                    "arxiv_id": aid,
                    "title": result.get("title", ""),
                    "source": "x_nitter",
                    "community_buzz": 10,
                })
        logger.info("X/Nitter: %d papers", len(papers))
        return papers
    except Exception as e:
        logger.warning("X/Nitter fetch failed: %s", e)
        if tracker:
            tracker.record_attempt(searxng_url, success=False, response_code=0)
        return []


def fetch_arxiv_rss_fallback() -> list[dict]:
    import xml.etree.ElementTree as ET
    papers = []
    for category in ["cs.AI", "cs.LG", "cs.CL"]:
        try:
            resp = httpx.get(
                f"https://rss.arxiv.org/rss/{category}",
                headers={"User-Agent": f"JobPulse/1.0 (mailto:{_get_email()})"},
                timeout=15,
            )
            resp.raise_for_status()
            root = ET.fromstring(resp.text)
            for item in root.findall(".//item"):
                link = item.findtext("link", "")
                ids = _extract_arxiv_ids(link)
                if ids:
                    papers.append({
                        "arxiv_id": ids[0],
                        "title": item.findtext("title", ""),
                        "source": "arxiv_rss",
                        "community_buzz": 0,
                    })
        except Exception as e:
            logger.warning("arXiv RSS %s failed: %s", category, e)
    logger.info("arXiv RSS fallback: %d papers", len(papers))
    return papers


def enrich_from_semantic_scholar(papers: list[dict]) -> list[dict]:
    for paper in papers:
        try:
            resp = httpx.get(
                f"https://api.semanticscholar.org/graph/v1/paper/ARXIV:{paper['arxiv_id']}",
                params={"fields": "title,abstract,citationCount,authors,year"},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                paper["abstract"] = data.get("abstract", "")
                paper["citation_count"] = data.get("citationCount", 0)
                paper["authors"] = [a.get("name", "") for a in data.get("authors", [])]
                paper["year"] = data.get("year")
            time.sleep(0.1)  # courtesy rate limit
        except Exception:
            pass
    return papers


def discover_trending_papers() -> list[dict]:
    """Main entry point: discover papers from community, enrich, return."""
    all_papers = []
    all_papers.extend(fetch_huggingface_daily())
    all_papers.extend(fetch_reddit_papers())
    all_papers.extend(fetch_hackernews_papers())
    all_papers.extend(fetch_papers_with_code())
    all_papers.extend(fetch_x_via_searxng())

    unique = dedup_by_arxiv_id(all_papers)
    logger.info("Discovery: %d total → %d unique papers", len(all_papers), len(unique))

    if not unique:
        logger.warning("All community sources empty, falling back to arXiv RSS")
        unique = fetch_arxiv_rss_fallback()

    enriched = enrich_from_semantic_scholar(unique)
    return enriched
