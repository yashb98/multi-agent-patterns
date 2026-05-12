from research_journal.summarizer import extract_facts
from jobpulse.papers.models import Paper
from research_journal.models import ExtractedFacts, BenchResult


def test_extract_uses_abstract_when_pdf_fails(monkeypatch):
    monkeypatch.setattr("research_journal.summarizer._download_pdf_text",
                        lambda url: "")
    monkeypatch.setattr(
        "research_journal.summarizer._llm_extract",
        lambda text: ExtractedFacts(
            problem="x", method_steps=["a", "b"],
            architecture_details={"backbone": "Llama-3-8B"},
            benchmarks=[BenchResult(name="MMLU", metric="acc", value=72.3)],
            ablations=["ablation 1"], limitations=["lim 1"],
            key_insight="k", raw_excerpts=["abstract excerpt"],
        ),
    )
    paper = Paper(arxiv_id="x", title="t", authors=["A"], abstract="A meaty abstract.",
                  categories=["cs.CL"], pdf_url="https://example.com/x.pdf",
                  arxiv_url="", published_at="2026-01-01")
    facts = extract_facts(paper)
    assert facts.benchmarks[0].name == "MMLU"
    assert len(facts.raw_excerpts) >= 1


def test_extract_full_pdf_path(monkeypatch):
    monkeypatch.setattr("research_journal.summarizer._download_pdf_text",
                        lambda url: "Full PDF text here. Lots of content.")
    captured_text = []
    def fake_extract(text):
        captured_text.append(text)
        return ExtractedFacts(
            problem="x", method_steps=["a"], architecture_details={},
            benchmarks=[], ablations=[], limitations=[], key_insight="k",
            raw_excerpts=["full text excerpt"],
        )
    monkeypatch.setattr("research_journal.summarizer._llm_extract", fake_extract)
    paper = Paper(arxiv_id="x", title="t", authors=["A"], abstract="abs",
                  categories=["cs.CL"], pdf_url="https://example.com/x.pdf",
                  arxiv_url="", published_at="2026-01-01")
    extract_facts(paper)
    assert "Full PDF text" in captured_text[0]
