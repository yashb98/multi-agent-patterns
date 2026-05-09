import pytest
from research_journal.results_filter import classify_results
from jobpulse.papers.models import Paper
from research_journal.models import PaperTypeClassification


def _paper(title: str, abstract: str) -> Paper:
    return Paper(arxiv_id="0", title=title, authors=["X"], abstract=abstract,
                 categories=["cs.CL"], pdf_url="", arxiv_url="", published_at="2026-01-01")


def test_passes_research_with_numbers(monkeypatch):
    fake = PaperTypeClassification(
        has_results=True, paper_type="research",
        reason="reports +3.2 on MMLU", confidence=0.92,
    )
    monkeypatch.setattr("research_journal.results_filter._llm_classify", lambda p: fake)
    out = classify_results(_paper("X", "We achieve +3.2 on MMLU and 84.1 F1 on BoolQ."))
    assert out.has_results is True
    assert out.paper_type == "research"


def test_drops_position_paper(monkeypatch):
    fake = PaperTypeClassification(
        has_results=False, paper_type="position",
        reason="argument; no experiments", confidence=0.95,
    )
    monkeypatch.setattr("research_journal.results_filter._llm_classify", lambda p: fake)
    out = classify_results(_paper("Position: We Need Better Evaluation", "We argue..."))
    assert out.has_results is False
    assert out.paper_type == "position"


def test_low_confidence_falls_through(monkeypatch):
    """Confidence < 0.6 → mark has_results=True (don't hard-drop on uncertainty)."""
    fake = PaperTypeClassification(
        has_results=False, paper_type="research",
        reason="unclear", confidence=0.45,
    )
    monkeypatch.setattr("research_journal.results_filter._llm_classify", lambda p: fake)
    out = classify_results(_paper("X", "..."))
    # The filter should NOT hard-drop on low confidence — return original but mark unknown
    assert out.has_results is True or out.confidence >= 0.6


def test_empty_abstract_drops(monkeypatch):
    out = classify_results(_paper("Title only", ""))
    assert out.has_results is False
    assert "empty abstract" in out.reason.lower()
