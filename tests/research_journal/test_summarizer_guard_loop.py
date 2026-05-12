from research_journal.summarizer import guard_summary, summarize_paper
from jobpulse.papers.models import Paper
from research_journal.models import ExtractedFacts, BenchResult


def _facts() -> ExtractedFacts:
    return ExtractedFacts(
        problem="x", method_steps=["s"], architecture_details={},
        benchmarks=[BenchResult(name="MMLU", metric="acc", value=72.3)],
        ablations=[], limitations=[], key_insight="k",
        raw_excerpts=["the model achieves 72.3 on MMLU"],
    )


def test_guard_passes_when_all_grounded(monkeypatch):
    monkeypatch.setattr("research_journal.summarizer.extract_claims_from_summary",
                        lambda md: ["the model achieves 72.3 on MMLU"])
    md = "## Results\nThe model achieves 72.3 on MMLU."
    grounded, failed = guard_summary(md, _facts())
    assert grounded is True
    assert failed == []


def test_guard_flags_ungrounded_claims(monkeypatch):
    monkeypatch.setattr("research_journal.summarizer.extract_claims_from_summary",
                        lambda md: ["Trained on 1.5T tokens.", "Achieves 72.3 on MMLU."])
    md = "fake summary"
    grounded, failed = guard_summary(md, _facts())
    assert grounded is False
    assert "1.5T tokens" in failed[0]


def test_summarize_paper_regenerates_then_accepts(monkeypatch):
    """If the guard fails the first time, writer is called again with avoid hints."""
    write_calls = []
    def fake_write(paper, facts, avoid=None):
        write_calls.append(avoid or [])
        return "v" + str(len(write_calls))
    monkeypatch.setattr("research_journal.summarizer._write_with_avoid", fake_write)
    monkeypatch.setattr("research_journal.summarizer.extract_facts", lambda p: _facts())
    monkeypatch.setattr(
        "research_journal.summarizer.extract_claims_from_summary",
        lambda md: ["Trained on 1.5T tokens."],
    )
    paper = Paper(arxiv_id="x", title="t", authors=["A"], abstract="abs",
                  categories=["cs.CL"], pdf_url="", arxiv_url="", published_at="2026-01-01")
    summary, claims_grounded = summarize_paper(paper)
    assert len(write_calls) == 2  # initial + 1 regen
    assert claims_grounded is False  # second attempt also fails
    assert summary == "v2"
