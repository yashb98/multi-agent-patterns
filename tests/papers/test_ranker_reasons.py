"""Tests for attach_rank_reasons — LLM rank_reason per top-N pick."""

from jobpulse.papers.ranker import attach_rank_reasons
from jobpulse.papers.models import RankedPaper, FactCheckResult


def test_attach_reasons_calls_llm_per_paper(monkeypatch):
    monkeypatch.setattr(
        "jobpulse.papers.ranker._llm_rank_reason",
        lambda paper, lens: f"REASON FOR {paper.arxiv_id}",
    )
    papers = [_rp("a"), _rp("b")]
    out = attach_rank_reasons(papers, lens="daily")
    assert out[0].rank_reason == "REASON FOR a"
    assert out[1].rank_reason == "REASON FOR b"


def _rp(arxiv_id: str) -> RankedPaper:
    return RankedPaper(
        arxiv_id=arxiv_id, title=f"Paper {arxiv_id}", authors=["X"], abstract="a",
        categories=["cs.CL"], pdf_url="", arxiv_url="", published_at="2026-01-01",
        impact_score=8.0, summary="s",
    )
