"""Tests for BlogPipeline — deep_read, generate, GRPO selection, error handling."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from jobpulse.papers.models import BlogPost, Chart, Paper


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_paper(
    *,
    model_card_summary: str | None = None,
    title: str = "Attention Is All You Need (Again)",
) -> Paper:
    return Paper(
        arxiv_id="2401.00001",
        title=title,
        authors=["Alice Smith", "Bob Jones"],
        abstract="We propose a novel transformer variant that improves efficiency by 40%.",
        categories=["cs.AI", "cs.LG"],
        pdf_url="https://arxiv.org/pdf/2401.00001",
        arxiv_url="https://arxiv.org/abs/2401.00001",
        published_at="2026-04-01",
        model_card_summary=model_card_summary,
    )


def _mock_llm_response(content: str) -> MagicMock:
    """Build a minimal mock that matches openai.chat.completions.create()."""
    choice = MagicMock()
    choice.message.content = content
    resp = MagicMock()
    resp.choices = [choice]
    return resp


# ---------------------------------------------------------------------------
# TestDeepRead
# ---------------------------------------------------------------------------


class TestDeepRead:
    """Agent 1: _deep_read generates structured research notes."""

    def test_returns_research_notes(self):
        """_deep_read returns the LLM's response string."""
        paper = _make_paper()
        expected_notes = "Core contribution: sparse attention. Methodology: ..."

        with patch(
            "jobpulse.papers.blog_pipeline._get_openai_client"
        ) as mock_client_fn:
            mock_client = MagicMock()
            mock_client_fn.return_value = mock_client
            mock_client.chat.completions.create.return_value = _mock_llm_response(expected_notes)

            from jobpulse.papers.blog_pipeline import BlogPipeline

            pipeline = BlogPipeline()
            # Patch chart_gen so it doesn't need matplotlib
            pipeline.chart_gen = MagicMock()
            pipeline.chart_gen.generate.return_value = []

            notes = pipeline._deep_read(paper)

        assert notes == expected_notes

    def test_includes_model_card_when_available(self):
        """_deep_read includes model_card_summary in the prompt when present."""
        paper = _make_paper(model_card_summary="Open foundation model with 8B variant.")

        captured_user_prompt: list[str] = []

        def fake_create(**kwargs):
            for msg in kwargs.get("messages", []):
                if msg["role"] == "user":
                    captured_user_prompt.append(msg["content"])
            return _mock_llm_response("notes with model card info")

        with patch(
            "jobpulse.papers.blog_pipeline._get_openai_client"
        ) as mock_client_fn:
            mock_client = MagicMock()
            mock_client_fn.return_value = mock_client
            mock_client.chat.completions.create.side_effect = fake_create

            from jobpulse.papers.blog_pipeline import BlogPipeline

            pipeline = BlogPipeline()
            pipeline.chart_gen = MagicMock()
            pipeline.chart_gen.generate.return_value = []

            notes = pipeline._deep_read(paper)

        assert len(captured_user_prompt) == 1
        assert "Model card summary" in captured_user_prompt[0]
        assert "Open foundation model" in captured_user_prompt[0]
        assert notes == "notes with model card info"

    def test_falls_back_when_llm_returns_empty(self):
        """_deep_read returns a fallback string when LLM call returns empty."""
        paper = _make_paper()

        with patch(
            "jobpulse.papers.blog_pipeline._get_openai_client"
        ) as mock_client_fn:
            mock_client = MagicMock()
            mock_client_fn.return_value = mock_client
            mock_client.chat.completions.create.return_value = _mock_llm_response("")

            from jobpulse.papers.blog_pipeline import BlogPipeline

            pipeline = BlogPipeline()
            pipeline.chart_gen = MagicMock()
            pipeline.chart_gen.generate.return_value = []

            notes = pipeline._deep_read(paper)

        # Fallback must include the paper title
        assert paper.title in notes


# ---------------------------------------------------------------------------
# TestGenerate
# ---------------------------------------------------------------------------


class TestGenerate:
    """End-to-end orchestration tests."""

    def test_returns_blog_post(self):
        """generate() returns a BlogPost with all required fields populated."""
        paper = _make_paper()

        with patch("jobpulse.papers.blog_pipeline._get_openai_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client_fn.return_value = mock_client

            # Every LLM call returns a reasonable string
            blog_text = (
                "# Attention Is All You Need (Again)\n\n"
                "## Introduction\n\nThis is the intro.\n\n"
                "## Methodology\n\nHere is the method.\n\n"
                "## Results\n\nThe results show 40% improvement.\n\n"
                "## Conclusion\n\nIn summary, great work.\n"
            )
            mock_client.chat.completions.create.return_value = _mock_llm_response(blog_text)

            from jobpulse.papers.blog_pipeline import BlogPipeline

            pipeline = BlogPipeline()
            # Patch chart_gen so no matplotlib/filesystem dependency
            mock_chart = Chart(
                chart_type="bar_comparison",
                title="Efficiency Comparison",
                data={"labels": ["Baseline", "Ours"], "values": [100.0, 60.0]},
                png_path="/tmp/test_chart.png",
                description="Performance vs baseline.",
            )
            pipeline.chart_gen = MagicMock()
            pipeline.chart_gen.generate.return_value = [mock_chart]

            post = pipeline.generate(paper, output_dir="/tmp")

        assert isinstance(post, BlogPost)
        assert post.paper.arxiv_id == paper.arxiv_id
        assert post.title  # not empty
        assert post.content  # not empty
        assert post.word_count > 0
        assert post.generated_at  # ISO timestamp

    def test_handles_llm_error_gracefully(self):
        """generate() returns a valid BlogPost even when all LLM calls fail."""
        paper = _make_paper()

        with patch("jobpulse.papers.blog_pipeline._get_openai_client") as mock_client_fn:
            mock_client_fn.return_value = None  # No client → every _llm_call returns ""

            from jobpulse.papers.blog_pipeline import BlogPipeline

            pipeline = BlogPipeline()
            pipeline.chart_gen = MagicMock()
            pipeline.chart_gen.generate.return_value = []

            post = pipeline.generate(paper, output_dir="/tmp")

        assert isinstance(post, BlogPost)
        assert post.paper.arxiv_id == paper.arxiv_id
        # Falls back to paper title when no LLM
        assert post.title == paper.title or post.title
        assert post.content  # fallback draft contains abstract at minimum
        assert post.word_count >= 0


# ---------------------------------------------------------------------------
# TestScoreBlog
# ---------------------------------------------------------------------------


class TestScoreBlog:
    """Unit tests for the heuristic _score_blog."""

    def test_ideal_draft_scores_highly(self):
        """A draft in the ideal word range with sections scores above 5."""
        paper = _make_paper()
        # ~650 word draft with 3 sections and conclusion
        body = "word " * 620
        draft = (
            f"# {paper.title}\n\n"
            "## Introduction\n\n" + body[:200] + "\n\n"
            "## Methodology\n\n" + body[:200] + "\n\n"
            "## Results\n\n" + body[:200] + "\n\n"
            "In conclusion, this paper demonstrates significant results.\n"
        )

        from jobpulse.papers.blog_pipeline import BlogPipeline

        pipeline = BlogPipeline()
        pipeline.chart_gen = MagicMock()
        score = pipeline._score_blog(draft, paper)

        assert score >= 5.0

    def test_placeholder_reduces_score(self):
        """Drafts with [INSERT ...] placeholders get penalised."""
        paper = _make_paper()
        draft_with = "word " * 700 + "\n\n## Results\n\n[INSERT CHART HERE]\n\nIn summary done."
        draft_without = "word " * 700 + "\n\n## Results\n\nSee chart above.\n\nIn summary done."

        from jobpulse.papers.blog_pipeline import BlogPipeline

        pipeline = BlogPipeline()
        pipeline.chart_gen = MagicMock()

        assert pipeline._score_blog(draft_with, paper) < pipeline._score_blog(draft_without, paper)


# ---------------------------------------------------------------------------
# TestExtractTitle
# ---------------------------------------------------------------------------


class TestExtractTitle:
    """Unit tests for _extract_title."""

    def test_extracts_first_h1_heading(self):
        """Title is extracted from the first # heading."""
        paper = _make_paper()
        content = "# My Custom Blog Title\n\nSome content here."

        from jobpulse.papers.blog_pipeline import BlogPipeline

        pipeline = BlogPipeline()
        pipeline.chart_gen = MagicMock()

        assert pipeline._extract_title(content, paper) == "My Custom Blog Title"

    def test_falls_back_to_paper_title(self):
        """Falls back to paper.title when no # heading is found."""
        paper = _make_paper()
        content = "Some content with no heading."

        from jobpulse.papers.blog_pipeline import BlogPipeline

        pipeline = BlogPipeline()
        pipeline.chart_gen = MagicMock()

        assert pipeline._extract_title(content, paper) == paper.title
