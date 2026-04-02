"""Notion Weekly Papers Agent — wrapper delegating to jobpulse.papers.PapersPipeline.

Runs Monday 8:33am via runner. Replaces the broken original that imported
non-existent functions (fast_score, llm_rank).
"""

import asyncio
from shared.logging_config import get_logger
from jobpulse import telegram_agent

logger = get_logger(__name__)


def create_weekly_page(trigger: str = "cron_monday") -> str:
    from jobpulse.papers import PapersPipeline
    pipeline = PapersPipeline()
    try:
        digest = asyncio.run(pipeline.weekly_digest())
        if digest:
            try:
                telegram_agent.send_research(digest)
            except Exception as e:
                logger.warning("Telegram send failed: %s", e)
        return digest
    except Exception as e:
        logger.error("Weekly papers digest failed: %s", e)
        return f"Error: {e}"
