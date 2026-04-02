"""Webhook server — replaces long-polling with push-based Telegram updates."""

import os
from fastapi import FastAPI, Request
from shared.logging_config import get_logger
from jobpulse.command_router import classify
from jobpulse.config import TELEGRAM_CHAT_ID, TELEGRAM_BOT_TOKEN

logger = get_logger(__name__)

USE_SWARM = os.getenv("JOBPULSE_SWARM", "true").lower() in ("true", "1", "yes")
if USE_SWARM:
    from jobpulse.swarm_dispatcher import dispatch
else:
    from jobpulse.dispatcher import dispatch

app = FastAPI(
    title="JobPulse API",
    version="1.0.0",
    description="JobPulse automation — papers, GitHub, health, and Telegram webhook.",
)

# Mount sub-routers
from jobpulse.health_api import health_router
from jobpulse.analytics_api import analytics_router
app.include_router(health_router)
app.include_router(analytics_router)


# ── Papers endpoints ──

@app.get("/api/papers/fetch", tags=["papers"])
def fetch_papers_endpoint(max_results: int = 20):
    """Fetch latest AI papers from arXiv."""
    from jobpulse.arxiv_agent import fetch_papers
    papers = fetch_papers(max_results=max_results)
    return {"count": len(papers), "papers": papers}


@app.get("/api/papers/digest", tags=["papers"])
def get_papers_digest():
    """Get today's ranked paper digest."""
    from jobpulse.arxiv_agent import build_digest
    digest = build_digest()
    return {"digest": digest, "length": len(digest)}


@app.get("/api/papers/stats", tags=["papers"])
def get_papers_stats():
    """Paper reading stats — total, read, unread, this week."""
    from jobpulse.arxiv_agent import get_reading_stats
    return get_reading_stats()


@app.get("/api/papers/{index}", tags=["papers"])
def get_paper_detail(index: int):
    """Get full details for a specific paper by index (1-based)."""
    from jobpulse.arxiv_agent import get_paper_by_index
    from datetime import date
    paper = get_paper_by_index(date.today().isoformat(), index)
    if paper is None:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=404, content={"error": f"No paper #{index} in today's digest"})
    return paper


@app.post("/api/papers/blog/{index}", tags=["papers"])
async def generate_paper_blog(index: int):
    """Generate a blog post for paper at given index."""
    try:
        from jobpulse.papers import PapersPipeline
        pipeline = PapersPipeline()
        blog = pipeline.generate_blog(index)
        return {"title": blog.title, "word_count": blog.word_count, "grpo_score": blog.grpo_score}
    except ValueError as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=str(e))


# ── GitHub endpoints ──

@app.get("/api/github/commits", tags=["github"])
def get_github_commits():
    """Get yesterday's commit activity across all repos."""
    from jobpulse.github_agent import get_yesterday_commits
    return get_yesterday_commits(trigger="api_call")


@app.get("/api/github/trending", tags=["github"])
def get_github_trending(count: int = 5):
    """Get trending GitHub repos."""
    from jobpulse.github_agent import get_trending_repos
    return get_trending_repos(count=count)


@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    """Handle incoming Telegram webhook updates."""
    data = await request.json()

    msg = data.get("message", {})
    from_id = str(msg.get("from", {}).get("id", ""))
    text = msg.get("text", "").strip()

    if from_id != TELEGRAM_CHAT_ID or not text:
        return {"ok": True}
    if text.lower() in ("hi", "hello", "hey"):
        return {"ok": True}

    logger.info("Webhook got: %s", text[:80])

    cmd = classify(text)
    reply = dispatch(cmd)

    from jobpulse.platforms.telegram_adapter import TelegramAdapter
    adapter = TelegramAdapter()
    adapter.send_message(reply)

    return {"ok": True}


def register_webhook(url: str):
    """Register webhook URL with Telegram API."""
    import httpx
    resp = httpx.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook",
        json={"url": f"{url}/webhook/telegram"},
        timeout=15,
    )
    logger.info("Webhook registered: %s", resp.json())
    return resp.json()


def delete_webhook():
    """Remove webhook (switch back to polling)."""
    import httpx
    resp = httpx.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteWebhook",
        timeout=15,
    )
    logger.info("Webhook deleted: %s", resp.json())
    return resp.json()
