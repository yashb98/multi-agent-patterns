"""Tests for PaperRanker — fast_score, llm_rank, extract_themes, JSON parsing."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from jobpulse.papers.models import Paper, RankedPaper
from jobpulse.papers.ranker import (
    _extract_json_array,
    extract_themes,
    fast_score,
    llm_rank,
    summarize_and_verify,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_paper(
    arxiv_id: str = "2401.00001",
    title: str = "Test Paper",
    authors: list[str] | None = None,
    abstract: str = "An abstract.",
    categories: list[str] | None = None,
    hf_upvotes: int | None = None,
    linked_models: list[str] | None = None,
    linked_datasets: list[str] | None = None,
    github_url: str = "",
    github_stars: int = 0,
    s2_citation_count: int = 0,
    community_buzz: int = 0,
    sources: list[str] | None = None,
) -> Paper:
    return Paper(
        arxiv_id=arxiv_id,
        title=title,
        authors=authors or ["Alice", "Bob", "Charlie"],
        abstract=abstract,
        categories=categories or ["cs.AI"],
        pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
        arxiv_url=f"https://arxiv.org/abs/{arxiv_id}",
        published_at="2026-04-01",
        hf_upvotes=hf_upvotes,
        linked_models=linked_models or [],
        linked_datasets=linked_datasets or [],
        github_url=github_url,
        github_stars=github_stars,
        s2_citation_count=s2_citation_count,
        community_buzz=community_buzz,
        sources=sources or [],
    )


def _mock_openai_response(content: str) -> MagicMock:
    """Build a minimal OpenAI-like response mock."""
    choice = MagicMock()
    choice.message.content = content
    response = MagicMock()
    response.choices = [choice]
    return response


# ---------------------------------------------------------------------------
# fast_score
# ---------------------------------------------------------------------------


class TestFastScore:
    def test_cs_ai_gets_category_bonus(self):
        paper = make_paper(categories=["cs.AI"])
        score = fast_score(paper)
        # cs.AI = 2.0, recency = 0.5 → minimum 2.5 (no upvotes/models/github/buzz/citations)
        assert score >= 2.5

    def test_unknown_category_gets_no_bonus(self):
        paper_ai = make_paper(categories=["cs.AI"])
        paper_other = make_paper(categories=["math.CO"])
        assert fast_score(paper_ai) > fast_score(paper_other)

    def test_hf_upvotes_over_50_boost(self):
        base = make_paper(hf_upvotes=None)
        high = make_paper(hf_upvotes=51)
        assert fast_score(high) >= fast_score(base) + 1.5

    def test_hf_upvotes_21_to_50_boost(self):
        base = make_paper(hf_upvotes=None)
        mid = make_paper(hf_upvotes=25)
        diff = fast_score(mid) - fast_score(base)
        assert diff == pytest.approx(1.0)

    def test_hf_upvotes_below_5_no_boost(self):
        base = make_paper(hf_upvotes=None)
        low = make_paper(hf_upvotes=3)
        assert fast_score(low) == pytest.approx(fast_score(base))

    def test_linked_models_boost(self):
        base = make_paper(linked_models=[])
        one_model = make_paper(linked_models=["meta-llama/Llama-3"])
        two_models = make_paper(linked_models=["model-a", "model-b"])
        three_models = make_paper(linked_models=["a", "b", "c"])

        # Any models gives 0.5
        assert fast_score(one_model) >= fast_score(base) + 0.5
        # Two or more models still gives 0.5 (datasets field empty, so capped at 0.5)
        assert fast_score(two_models) == pytest.approx(fast_score(one_model))
        # Three models same as two — cap still 0.5 without datasets
        assert fast_score(three_models) == pytest.approx(fast_score(two_models))

    def test_github_url_boost(self):
        base = make_paper(github_url="")
        with_gh = make_paper(github_url="https://github.com/user/repo")
        assert fast_score(with_gh) >= fast_score(base) + 0.5

    def test_max_score_capped_at_10(self):
        paper = make_paper(
            categories=["cs.AI"],
            hf_upvotes=100,
            linked_models=["m1", "m2", "m3"],
            linked_datasets=["d1"],
            github_url="https://github.com/org/repo",
            github_stars=100,
            s2_citation_count=30,
            community_buzz=200,
            sources=["huggingface", "hackernews", "reddit"],
        )
        assert fast_score(paper) <= 10.0

    def test_recency_bonus_always_applied(self):
        paper = make_paper(categories=["math.CO"], authors=["Alice"])
        # Only recency (0.5) should be awarded — no category, no other bonuses
        assert fast_score(paper) >= 0.5

    def test_category_weights_ordering(self):
        """cs.AI / cs.LG outrank cs.CL which outranks cs.MA."""
        ai_paper = make_paper(categories=["cs.AI"], authors=["A"])
        cl_paper = make_paper(categories=["cs.CL"], authors=["A"])
        ma_paper = make_paper(categories=["cs.MA"], authors=["A"])
        assert fast_score(ai_paper) > fast_score(cl_paper) > fast_score(ma_paper)


# ---------------------------------------------------------------------------
# TestFastScoreV2 — new signals
# ---------------------------------------------------------------------------


class TestFastScoreV2:
    def test_community_buzz_high(self):
        base = make_paper(community_buzz=0)
        high = make_paper(community_buzz=150)
        assert fast_score(high) > fast_score(base)

    def test_s2_citations_boost(self):
        base = make_paper(s2_citation_count=0)
        cited = make_paper(s2_citation_count=25)
        assert fast_score(cited) > fast_score(base)

    def test_github_repo_boost(self):
        base = make_paper(github_url="")
        with_repo = make_paper(github_url="https://github.com/org/repo", github_stars=100)
        assert fast_score(with_repo) > fast_score(base)

    def test_multi_source_bonus(self):
        one = make_paper(sources=["huggingface"])
        three = make_paper(sources=["huggingface", "hackernews", "reddit"])
        assert fast_score(three) > fast_score(one)

    def test_linked_datasets_boost(self):
        base = make_paper(linked_datasets=[])
        with_ds = make_paper(linked_datasets=["ds-1"])
        assert fast_score(with_ds) > fast_score(base)

    def test_max_still_capped_at_10(self):
        paper = make_paper(
            categories=["cs.AI"], hf_upvotes=100, linked_models=["m1", "m2"],
            linked_datasets=["d1"], github_url="https://github.com/x/y",
            github_stars=100, s2_citation_count=30, community_buzz=200,
            sources=["huggingface", "hackernews", "reddit"],
        )
        assert fast_score(paper) <= 10.0


# ---------------------------------------------------------------------------
# _extract_json_array
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Raw valid JSON array
        ('[{"a": 1}]', [{"a": 1}]),
        # Markdown fenced JSON
        ('```json\n[{"b": 2}]\n```', [{"b": 2}]),
        # Markdown fence without language specifier
        ('```\n["x","y"]\n```', ["x", "y"]),
        # Empty array
        ("[]", []),
        # Invalid JSON string
        ("not json at all", []),
        # Empty string
        ("", []),
        # JSON object (not array) → returns []
        ('{"key": "value"}', []),
    ],
)
def test_extract_json_array_parametrized(raw: str, expected: list):
    assert _extract_json_array(raw) == expected


# ---------------------------------------------------------------------------
# llm_rank
# ---------------------------------------------------------------------------


class TestLlmRank:
    def _make_papers(self, n: int = 5) -> list[Paper]:
        return [
            make_paper(arxiv_id=f"2401.{i:05d}", title=f"Paper {i}", categories=["cs.AI"])
            for i in range(n)
        ]

    def test_empty_input_returns_empty(self):
        assert llm_rank([]) == []

    def test_fallback_when_no_api_key(self):
        papers = self._make_papers(5)
        with patch("jobpulse.papers.ranker._get_openai_client", return_value=None):
            result = llm_rank(papers, top_n=3)
        assert len(result) == 3
        assert all(isinstance(r, RankedPaper) for r in result)

    def test_fallback_on_api_error(self):
        papers = self._make_papers(5)
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = RuntimeError("API down")
        with patch("jobpulse.papers.ranker._get_openai_client", return_value=mock_client):
            result = llm_rank(papers, top_n=3)
        assert len(result) == 3
        assert all(isinstance(r, RankedPaper) for r in result)

    def test_fallback_on_invalid_json(self):
        papers = self._make_papers(5)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _mock_openai_response("garbage")
        with patch("jobpulse.papers.ranker._get_openai_client", return_value=mock_client):
            result = llm_rank(papers, top_n=3)
        assert len(result) == 3

    def test_returns_ranked_papers_from_llm(self):
        papers = self._make_papers(3)
        llm_json = json_str = (
            '[{"arxiv_id": "2401.00000", "impact_score": 9.0, "impact_reason": "Novel", '
            '"category_tag": "LLM", "key_technique": "LoRA", "practical_takeaway": "Fast fine-tuning"}, '
            '{"arxiv_id": "2401.00001", "impact_score": 7.5, "impact_reason": "Solid", '
            '"category_tag": "Agents", "key_technique": "RAG", "practical_takeaway": "Better retrieval"}, '
            '{"arxiv_id": "2401.00002", "impact_score": 6.0, "impact_reason": "Incremental", '
            '"category_tag": "Efficiency", "key_technique": "Pruning", "practical_takeaway": "Smaller models"}]'
        )
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _mock_openai_response(llm_json)
        with patch("jobpulse.papers.ranker._get_openai_client", return_value=mock_client):
            result = llm_rank(papers, top_n=3)
        assert len(result) == 3
        assert result[0].arxiv_id == "2401.00000"
        assert result[0].impact_score == pytest.approx(9.0)
        assert result[0].category_tag == "LLM"
        assert result[0].key_technique == "LoRA"

    def test_weekly_lens_uses_different_weights(self):
        """llm_rank does not error with weekly lens — prompt must include 'weekly'."""
        papers = self._make_papers(3)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _mock_openai_response("[]")
        with patch("jobpulse.papers.ranker._get_openai_client", return_value=mock_client):
            result = llm_rank(papers, top_n=3, lens="weekly")
        # Empty JSON → fallback
        assert len(result) == 3
        # Check the prompt included "weekly"
        call_args = mock_client.chat.completions.create.call_args
        prompt = call_args[1]["messages"][0]["content"]
        assert "weekly" in prompt.lower()

    def test_top_n_respected(self):
        papers = self._make_papers(10)
        with patch("jobpulse.papers.ranker._get_openai_client", return_value=None):
            result = llm_rank(papers, top_n=2)
        assert len(result) == 2

    def test_fast_score_populated_in_results(self):
        papers = self._make_papers(3)
        with patch("jobpulse.papers.ranker._get_openai_client", return_value=None):
            result = llm_rank(papers, top_n=3)
        for r in result:
            assert r.fast_score > 0.0


# ---------------------------------------------------------------------------
# extract_themes
# ---------------------------------------------------------------------------


class TestExtractThemes:
    def test_empty_input_returns_empty(self):
        assert extract_themes([]) == []

    def test_no_client_returns_empty(self):
        papers = [make_paper()]
        with patch("jobpulse.papers.ranker._get_openai_client", return_value=None):
            result = extract_themes(papers)
        assert result == []

    def test_returns_themes_list(self):
        papers = [make_paper(title=f"Paper on LLMs {i}") for i in range(5)]
        llm_response = '["Large Language Models", "Efficient Training", "Multimodal AI"]'
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _mock_openai_response(llm_response)
        with patch("jobpulse.papers.ranker._get_openai_client", return_value=mock_client):
            result = extract_themes(papers)
        assert result == ["Large Language Models", "Efficient Training", "Multimodal AI"]

    def test_fallback_on_api_error(self):
        papers = [make_paper()]
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = RuntimeError("Timeout")
        with patch("jobpulse.papers.ranker._get_openai_client", return_value=mock_client):
            result = extract_themes(papers)
        assert result == []

    def test_invalid_json_returns_empty(self):
        papers = [make_paper()]
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _mock_openai_response("not json")
        with patch("jobpulse.papers.ranker._get_openai_client", return_value=mock_client):
            result = extract_themes(papers)
        assert result == []


# ---------------------------------------------------------------------------
# summarize_and_verify
# ---------------------------------------------------------------------------


class TestSummarizeAndVerify:
    def test_no_client_returns_ranked_papers_without_summary(self):
        papers = [make_paper()]
        with patch("jobpulse.papers.ranker._get_openai_client", return_value=None):
            result = summarize_and_verify(papers)
        assert len(result) == 1
        assert isinstance(result[0], RankedPaper)
        assert result[0].summary == ""

    def test_with_client_calls_summarize(self):
        papers = [make_paper()]
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _mock_openai_response(
            "This paper proposes a novel approach."
        )
        with patch("jobpulse.papers.ranker._get_openai_client", return_value=mock_client):
            with patch("jobpulse.papers.ranker._verify_paper") as mock_verify:
                from jobpulse.papers.models import FactCheckResult
                mock_verify.return_value = FactCheckResult(score=8.0)
                result = summarize_and_verify(papers)
        assert result[0].summary == "This paper proposes a novel approach."
        assert result[0].fact_check is not None
        assert result[0].fact_check.score == pytest.approx(8.0)

    def test_fast_score_populated(self):
        papers = [make_paper(categories=["cs.AI"])]
        with patch("jobpulse.papers.ranker._get_openai_client", return_value=None):
            result = summarize_and_verify(papers)
        assert result[0].fast_score > 0.0
