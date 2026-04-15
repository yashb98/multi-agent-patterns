"""Tests for DigestBuilder — Telegram message formatting."""

from jobpulse.papers.digest import DigestBuilder
from jobpulse.papers.models import RankedPaper, FactCheckResult


def _make_ranked(arxiv_id: str, title: str, score: float, tag: str = "LLM", **kwargs):
    return RankedPaper(
        arxiv_id=arxiv_id, title=title, authors=["Alice", "Bob"],
        abstract="Abstract.", categories=["cs.AI"],
        pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
        arxiv_url=f"https://arxiv.org/abs/{arxiv_id}",
        published_at="2026-04-01", impact_score=score,
        category_tag=tag, key_technique="Technique",
        summary="WHAT: X. WHY: Y. HOW: Z. USE: W.",
        fact_check=FactCheckResult(score=9.0, total_claims=3, verified_count=3),
        **kwargs,
    )


class TestDailyFormat:
    def test_includes_paper_info(self):
        papers = [_make_ranked("2401.00001", "Test Paper", 8.5)]
        result = DigestBuilder().format_daily(papers)
        assert "Test Paper" in result
        assert "8.5" in result
        assert "[LLM]" in result
        assert "arxiv.org" in result

    def test_includes_hf_signals(self):
        papers = [_make_ranked("2401.00001", "Test", 8.0, hf_upvotes=42, linked_models=["m1", "m2"])]
        result = DigestBuilder().format_daily(papers)
        assert "42" in result
        assert "2 models" in result

    def test_empty_papers(self):
        result = DigestBuilder().format_daily([])
        assert "No papers" in result or "empty" in result.lower() or result == ""

    def test_command_hints(self):
        papers = [_make_ranked("2401.00001", "Test", 8.0)]
        result = DigestBuilder().format_daily(papers)
        assert "paper" in result.lower()
        assert "blog" in result.lower()


class TestDailyFormatNewSignals:
    def test_includes_github_link(self):
        papers = [_make_ranked("2401.00001", "Test", 8.0, github_url="https://github.com/org/repo", github_stars=42)]
        result = DigestBuilder().format_daily(papers)
        assert "github.com" in result
        assert "42" in result

    def test_includes_s2_citations(self):
        papers = [_make_ranked("2401.00001", "Test", 8.0, s2_citation_count=25)]
        result = DigestBuilder().format_daily(papers)
        assert "25" in result
        assert "cit" in result.lower()

    def test_includes_source_attribution(self):
        papers = [_make_ranked("2401.00001", "Test", 8.0, sources=["huggingface", "hackernews", "reddit"])]
        result = DigestBuilder().format_daily(papers)
        assert "HuggingFace" in result or "huggingface" in result


class TestWeeklyFormat:
    def test_includes_themes(self):
        papers = [_make_ranked("2401.00001", "Test", 8.0)]
        themes = ["Efficiency dominates", "Agents converge"]
        result = DigestBuilder().format_weekly(papers, themes)
        assert "Efficiency dominates" in result
        assert "Agents converge" in result

    def test_includes_stats(self):
        papers = [_make_ranked("2401.00001", "Test", 8.0)]
        result = DigestBuilder().format_weekly(papers, [])
        assert "1" in result

    def test_empty_weekly(self):
        result = DigestBuilder().format_weekly([], [])
        assert isinstance(result, str)
