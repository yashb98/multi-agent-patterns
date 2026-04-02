"""PapersPipeline — orchestrator for the papers pipeline."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import jobpulse.papers.ranker as _ranker_module
from jobpulse.papers.fetcher import PaperFetcher
from jobpulse.papers.store import PaperStore
from jobpulse.papers.digest import DigestBuilder
from jobpulse.papers.blog_pipeline import BlogPipeline
from jobpulse.papers.notion_publisher import NotionPublisher
from jobpulse.papers.models import BlogPost, Paper, RankedPaper
from shared.logging_config import get_logger

logger = get_logger(__name__)


class PaperRanker:
    """Thin wrapper around the module-level functions in ranker.py."""

    def llm_rank(
        self,
        papers: list[Paper] | list[RankedPaper],
        top_n: int = 5,
        lens: str = "daily",
    ) -> list[RankedPaper]:
        return _ranker_module.llm_rank(papers, top_n=top_n, lens=lens)  # type: ignore[arg-type]

    def summarize_and_verify(self, papers: list[RankedPaper]) -> list[RankedPaper]:
        return _ranker_module.summarize_and_verify(papers)

    def extract_themes(self, papers: list[RankedPaper]) -> list[str]:
        return _ranker_module.extract_themes(papers)  # type: ignore[arg-type]


class PapersPipeline:
    def __init__(self, db_path: Path | None = None):
        self.fetcher = PaperFetcher()
        self.ranker = PaperRanker()
        self.store = PaperStore(db_path=db_path)
        self.digest = DigestBuilder()
        self.blog = BlogPipeline()
        self.notion = NotionPublisher()

    async def daily_digest(self, top_n: int = 5) -> str:
        today = datetime.now().strftime("%Y-%m-%d")
        papers = await self.fetcher.fetch_all()
        logger.info("Fetched %d papers from arXiv + HuggingFace", len(papers))
        ranked = self.ranker.llm_rank(papers, top_n=top_n)
        verified = self.ranker.summarize_and_verify(ranked)
        self.store.store(verified, digest_date=today)
        self.notion.publish_daily(verified, today)
        return self.digest.format_daily(verified, digest_date=today)

    async def weekly_digest(self, top_n: int = 7) -> str:
        stored = self.store.get_week(last_n_days=7)
        missed_dates = self.store.get_missed_dates(last_n_days=7)
        missed = await self.fetcher.fetch_missed(missed_dates)
        stored_ids = {p.arxiv_id for p in stored}
        new_papers = [p for p in missed if p.arxiv_id not in stored_ids]
        if new_papers:
            new_ranked = self.ranker.llm_rank(new_papers, top_n=len(new_papers))
            verified_new = self.ranker.summarize_and_verify(new_ranked)
            today = datetime.now().strftime("%Y-%m-%d")
            self.store.store(verified_new, digest_date=today)
            stored = stored + verified_new
        ranked = self.ranker.llm_rank(stored, top_n=top_n, lens="weekly")
        themes = self.ranker.extract_themes(ranked)
        self.notion.publish_weekly(ranked, themes)
        return self.digest.format_weekly(ranked, themes)

    def generate_blog(self, paper_index: int, digest_date: str = "") -> BlogPost:
        if not digest_date:
            digest_date = datetime.now().strftime("%Y-%m-%d")
        paper = self.store.get_by_index(digest_date, paper_index)
        if not paper:
            raise ValueError(f"No paper at index {paper_index} for {digest_date}")
        blog = self.blog.generate(paper)
        self.notion.publish_blog(blog)
        return blog
