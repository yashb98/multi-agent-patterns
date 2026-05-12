import pytest
from unittest.mock import MagicMock, AsyncMock
from jobpulse.papers.models import Paper, RankedPaper
from research_journal.models import VerificationBadge, PaperTypeClassification
from research_journal.pipeline import JournalPipeline


@pytest.mark.asyncio
async def test_daily_journal_end_to_end(monkeypatch, tmp_path):
    fake_papers = [Paper(arxiv_id=str(i), title=f"P{i}", authors=["A"], abstract="abs",
                         categories=["cs.CL"], pdf_url="", arxiv_url="", published_at="2026-01-01")
                   for i in range(15)]
    pipeline = JournalPipeline(db_path=tmp_path / "p.db")
    pipeline.fetcher.fetch_all = AsyncMock(return_value=fake_papers)
    pipeline.fetcher.enrich = AsyncMock(return_value=fake_papers)
    monkeypatch.setattr("research_journal.pipeline.classify_domain",
                        lambda p: ("core", 0.8, "matched") if int(p.arxiv_id) < 12 else ("tangent", 0.7, "tangent"))
    monkeypatch.setattr("research_journal.pipeline.classify_results",
                        lambda p: PaperTypeClassification(has_results=True, paper_type="research",
                                                          reason="ok", confidence=0.9))
    monkeypatch.setattr("research_journal.pipeline.summarize_paper",
                        lambda p: ("## TL;DR\nshort\n## Problem\nx\n## Method\ny\n## Key insight\nz\n## Results\nr\n## Limitations\nl", True))
    monkeypatch.setattr("research_journal.pipeline.verify_paper",
                        lambda p, has_results: VerificationBadge(
                            has_results=has_results, peer_reviewed=False, has_repo=False,
                            independent_citations=False, claims_grounded=False,
                        ))
    monkeypatch.setattr("research_journal.pipeline.publish_journal_to_notion",
                        lambda **kw: ["fake-id"])
    monkeypatch.setattr("research_journal.pipeline.send_telegram_message", lambda msg: None)
    # Pin the ranker so stable sort doesn't accidentally drop tangent papers.
    # llm_rank must return RankedPaper instances (not bare Paper) because
    # PaperStore.store() accesses paper.fact_check which only exists on RankedPaper.
    import research_journal.pipeline as _pipe
    monkeypatch.setattr(_pipe.pipeline_ranker, "llm_rank",
                        lambda papers, top_n, lens="daily": [
                            RankedPaper(**p.model_dump()) for p in papers[:top_n]
                        ])
    monkeypatch.setattr("research_journal.pipeline.attach_rank_reasons",
                        lambda papers, lens="daily": papers)

    # Use target_volume_max=15 so all 15 survivors fit, ensuring tangent papers
    # (arxiv_id 12-14) are not silently dropped by the top-N cap.
    result = await pipeline.daily_journal(target_volume_max=15)
    assert result["core_count"] >= 8
    assert result["tangent_count"] >= 1
