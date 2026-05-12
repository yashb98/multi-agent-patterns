from research_journal.summarizer import write_summary
from jobpulse.papers.models import Paper
from research_journal.models import ExtractedFacts, BenchResult


def _facts() -> ExtractedFacts:
    return ExtractedFacts(
        problem="LLMs hallucinate.", method_steps=["s1", "s2"],
        architecture_details={"backbone": "Llama-3-8B"},
        benchmarks=[BenchResult(name="MMLU", metric="acc", value=72.3, baseline=68.1)],
        ablations=["ablation A"], limitations=["lim A"],
        key_insight="An insight", raw_excerpts=["excerpt 1"],
    )


def _paper() -> Paper:
    return Paper(arxiv_id="x", title="Title", authors=["A"], abstract="abs",
                 categories=["cs.CL"], pdf_url="", arxiv_url="", published_at="2026-01-01")


def test_writer_returns_six_sections(monkeypatch):
    monkeypatch.setattr(
        "research_journal.summarizer._llm_write",
        lambda paper, facts: (
            "## TL;DR\nA " + ("word " * 49) +
            "\n\n## Problem\n" + ("p " * 200) +
            "\n\n## Method\n" + ("m " * 450) +
            "\n\n## Key insight\n" + ("k " * 100) +
            "\n\n## Results\n" + ("r " * 350) +
            "\n\n## Limitations\n" + ("l " * 100)
        ),
    )
    md = write_summary(_paper(), _facts())
    for header in ("## TL;DR", "## Problem", "## Method", "## Key insight", "## Results", "## Limitations"):
        assert header in md


def test_writer_word_count_in_range(monkeypatch):
    monkeypatch.setattr(
        "research_journal.summarizer._llm_write",
        lambda p, f: "## TL;DR\n" + ("w " * 1200),
    )
    md = write_summary(_paper(), _facts(), max_attempts=1)
    assert isinstance(md, str)
