"""Shared fixtures for papers pipeline tests."""

import pytest
from jobpulse.papers.models import Paper, RankedPaper, FactCheckResult


@pytest.fixture
def sample_paper():
    return Paper(
        arxiv_id="2401.00001",
        title="Attention Is All You Need (Again)",
        authors=["Alice Smith", "Bob Jones"],
        abstract="We propose a novel transformer variant that improves efficiency by 40%.",
        categories=["cs.AI", "cs.LG"],
        pdf_url="https://arxiv.org/pdf/2401.00001",
        arxiv_url="https://arxiv.org/abs/2401.00001",
        published_at="2026-04-01",
    )


@pytest.fixture
def sample_ranked_paper(sample_paper):
    return RankedPaper(
        **sample_paper.model_dump(),
        fast_score=7.5,
        impact_score=8.2,
        impact_reason="Novel efficiency improvement",
        category_tag="Efficiency",
        key_technique="Sparse attention with linear scaling",
        practical_takeaway="Can reduce inference cost by 40%",
        summary="Proposes sparse attention. Matters for cost. Uses linear scaling. Useful for production.",
        fact_check=FactCheckResult(score=9.0, total_claims=3, verified_count=3),
    )


@pytest.fixture
def sample_hf_paper():
    return Paper(
        arxiv_id="2401.00002",
        title="LLaMA 4: Open Foundation Model",
        authors=["Meta AI"],
        abstract="We release LLaMA 4, an open-weight foundation model.",
        categories=["cs.CL"],
        pdf_url="https://arxiv.org/pdf/2401.00002",
        arxiv_url="https://arxiv.org/abs/2401.00002",
        published_at="2026-04-01",
        source="both",
        hf_upvotes=180,
        linked_models=["meta-llama/Llama-4-8B", "meta-llama/Llama-4-70B"],
        linked_datasets=["tatsu-lab/alpaca"],
        model_card_summary="Open foundation model with 8B and 70B variants.",
    )


@pytest.fixture
def paper_store(tmp_path):
    from jobpulse.papers.store import PaperStore
    return PaperStore(db_path=tmp_path / "papers.db")
