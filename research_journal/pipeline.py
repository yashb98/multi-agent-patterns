"""JournalPipeline — daily orchestrator for the curated research journal."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from jobpulse.papers.fetcher import PaperFetcher
from jobpulse.papers.models import Paper
from jobpulse.papers.ranker import attach_rank_reasons
from jobpulse.papers.store import PaperStore
from jobpulse.papers import PaperRanker
from jobpulse.telegram_bots import send_research as send_telegram_message
from research_journal.delivery import publish_journal_to_notion, build_journal_telegram_digest
from research_journal.domain_filter import classify_domain
from research_journal.results_filter import classify_results
from research_journal.summarizer import summarize_paper
from research_journal.verifier import verify_paper
from shared.logging_config import get_logger

logger = get_logger(__name__)

# Module-level singleton so tests can monkeypatch pipeline_ranker.llm_rank
pipeline_ranker = PaperRanker()


class JournalPipeline:
    """Daily curated research journal — ML/LLM/SLM/VLM/finetune."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.fetcher = PaperFetcher()
        self.ranker = pipeline_ranker
        self.store = PaperStore(db_path=db_path)

    async def daily_journal(self, target_volume_max: int = 12) -> dict:
        today = datetime.now().strftime("%Y-%m-%d")
        papers = await self.fetcher.fetch_all()
        logger.info("daily_journal: fetched %d raw papers", len(papers))

        # Stage ② Domain classifier
        tagged: list[tuple[Paper, str]] = []
        for p in papers:
            tag, _, _ = classify_domain(p)
            if tag != "out":
                tagged.append((p, tag))
        logger.info("daily_journal: %d core+tangent after domain filter", len(tagged))

        # Stage ③ Hard filter on empirical results
        survivors: list[tuple[Paper, str, str]] = []
        for paper, tag in tagged:
            cls = classify_results(paper)
            if cls.has_results:
                survivors.append((paper, tag, cls.paper_type))
        logger.info("daily_journal: %d survived has_results hard filter", len(survivors))

        # Stage ④ Enrich + rank
        survivor_papers = [s[0] for s in survivors]
        enriched = await self.fetcher.enrich(survivor_papers)
        ranked = self.ranker.llm_rank(enriched, top_n=min(target_volume_max, len(enriched)))
        ranked = attach_rank_reasons(ranked, lens="daily")

        tag_by_id = {s[0].arxiv_id: s[1] for s in survivors}

        # Stages ⑤ Verify + ⑥ Summarize per paper
        published: list[tuple] = []
        for paper in ranked:
            cls_for_results = classify_results(paper)
            badge = verify_paper(paper, has_results=cls_for_results.has_results)
            summary, grounded = summarize_paper(paper)
            badge.claims_grounded = grounded
            domain_tag = tag_by_id.get(paper.arxiv_id, "core")
            published.append((paper, badge, summary, domain_tag))

        # Persist ranked papers (domain_tag/summary_long/verification are delivered via
        # publish_journal_to_notion and the Telegram digest; PaperStore only stores core model fields)
        self.store.store(ranked, digest_date=today)

        # Stage ⑦ Delivery
        page_ids = publish_journal_to_notion(
            items=published, digest_date=today,
        )
        digest_msg = build_journal_telegram_digest(
            items=[(p, b, t) for (p, b, _, t) in published],
            page_url_for=lambda aid: f"https://www.notion.so/{aid.replace('.', '')}",
        )
        send_telegram_message(digest_msg)

        core_count = sum(1 for _, _, _, t in published if t == "core")
        tangent_count = sum(1 for _, _, _, t in published if t == "tangent")
        return {"core_count": core_count, "tangent_count": tangent_count, "page_ids": page_ids}
