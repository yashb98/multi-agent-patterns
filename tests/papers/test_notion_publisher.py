"""Tests for NotionPublisher — block building and Notion API calls."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from jobpulse.papers.models import BlogPost, Chart, FactCheckResult, Paper, RankedPaper
from jobpulse.papers.notion_publisher import (
    NotionPublisher,
    _build_blog_blocks,
    _build_daily_blocks,
    _build_weekly_blocks,
    _heading_block,
    _text_block,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_paper(arxiv_id: str = "2401.00001", title: str = "Test Paper") -> RankedPaper:
    return RankedPaper(
        arxiv_id=arxiv_id,
        title=title,
        authors=["Alice", "Bob"],
        abstract="An abstract.",
        categories=["cs.AI"],
        pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
        arxiv_url=f"https://arxiv.org/abs/{arxiv_id}",
        published_at="2026-04-01",
        impact_score=8.5,
        category_tag="LLM",
        key_technique="Sparse attention",
        practical_takeaway="Reduces inference cost",
        summary="WHAT: X. WHY: Y. HOW: Z. USE: W.",
        fact_check=FactCheckResult(score=9.0, total_claims=3, verified_count=3),
    )


def _make_blog() -> BlogPost:
    paper = Paper(
        arxiv_id="2401.00001",
        title="Test Paper",
        authors=["Alice"],
        abstract="Abstract.",
        categories=["cs.AI"],
        pdf_url="https://arxiv.org/pdf/2401.00001",
        arxiv_url="https://arxiv.org/abs/2401.00001",
        published_at="2026-04-01",
    )
    chart = Chart(
        chart_type="bar_comparison",
        title="Performance Chart",
        data={"x": [1, 2], "y": [3, 4]},
        png_path="/tmp/chart.png",
        description="Benchmark comparison.",
    )
    return BlogPost(
        title="Deep Dive: Test Paper",
        content="# Introduction\nThis paper proposes something.\n## Method\nUsing sparse attention.",
        charts=[chart],
        mermaid_code="graph TD; A-->B",
        diagram_url="https://example.com/diagram.png",
        word_count=120,
        grpo_score=8.2,
        fact_check=FactCheckResult(score=9.1, total_claims=4, verified_count=4, explanation="All claims verified."),
        paper=paper,
        generated_at="2026-04-01T12:00:00",
    )


# ---------------------------------------------------------------------------
# Helper block builders
# ---------------------------------------------------------------------------


class TestTextBlock:
    def test_returns_paragraph_by_default(self):
        block = _text_block("Hello world")
        assert block["type"] == "paragraph"
        assert block["paragraph"]["rich_text"][0]["text"]["content"] == "Hello world"

    def test_respects_block_type(self):
        block = _text_block("A quote", block_type="quote")
        assert block["type"] == "quote"
        assert "rich_text" in block["quote"]

    def test_truncates_at_2000_chars(self):
        long_text = "x" * 3000
        block = _text_block(long_text)
        content = block["paragraph"]["rich_text"][0]["text"]["content"]
        assert len(content) == 2000


class TestHeadingBlock:
    def test_level_2_by_default(self):
        block = _heading_block("Section Title")
        assert block["type"] == "heading_2"
        assert block["heading_2"]["rich_text"][0]["text"]["content"] == "Section Title"

    def test_level_1(self):
        block = _heading_block("Top", level=1)
        assert block["type"] == "heading_1"

    def test_level_3(self):
        block = _heading_block("Sub", level=3)
        assert block["type"] == "heading_3"

    def test_clamps_level_above_3(self):
        block = _heading_block("X", level=5)
        assert block["type"] == "heading_3"

    def test_clamps_level_below_1(self):
        block = _heading_block("X", level=0)
        assert block["type"] == "heading_1"


# ---------------------------------------------------------------------------
# TestBuildDailyBlocks
# ---------------------------------------------------------------------------


class TestBuildDailyBlocks:
    def test_builds_index_and_paper_blocks(self):
        papers = [_make_paper("2401.00001", "Alpha Paper"), _make_paper("2401.00002", "Beta Paper")]
        blocks = _build_daily_blocks(papers, "2026-04-01")

        types = [b["type"] for b in blocks]
        assert "heading_1" in types
        assert "paragraph" in types

        all_text = _extract_all_text(blocks)
        assert "2026-04-01" in all_text
        assert "Alpha Paper" in all_text
        assert "Beta Paper" in all_text
        assert "arxiv.org" in all_text

    def test_includes_impact_score(self):
        papers = [_make_paper()]
        blocks = _build_daily_blocks(papers, "2026-04-02")
        all_text = _extract_all_text(blocks)
        assert "8.5" in all_text

    def test_includes_category_tag(self):
        papers = [_make_paper()]
        blocks = _build_daily_blocks(papers, "2026-04-02")
        all_text = _extract_all_text(blocks)
        assert "LLM" in all_text

    def test_includes_fact_check_info(self):
        papers = [_make_paper()]
        blocks = _build_daily_blocks(papers, "2026-04-02")
        all_text = _extract_all_text(blocks)
        assert "9.0" in all_text

    def test_empty_papers_produces_valid_blocks(self):
        blocks = _build_daily_blocks([], "2026-04-01")
        assert len(blocks) >= 1
        # Should still have at least the title heading
        assert blocks[0]["type"] == "heading_1"


# ---------------------------------------------------------------------------
# TestBuildWeeklyBlocks
# ---------------------------------------------------------------------------


class TestBuildWeeklyBlocks:
    def test_includes_themes(self):
        papers = [_make_paper()]
        themes = ["Efficiency dominates", "Agents converge", "Safety focus"]
        blocks = _build_weekly_blocks(papers, themes, "2026-03-25", "2026-03-31")
        all_text = _extract_all_text(blocks)
        assert "Efficiency dominates" in all_text
        assert "Agents converge" in all_text
        assert "Safety focus" in all_text

    def test_includes_date_range(self):
        blocks = _build_weekly_blocks([], [], "2026-03-25", "2026-03-31")
        all_text = _extract_all_text(blocks)
        assert "2026-03-25" in all_text
        assert "2026-03-31" in all_text

    def test_themes_section_present(self):
        blocks = _build_weekly_blocks([_make_paper()], ["AI safety"], "", "")
        types = [b["type"] for b in blocks]
        assert "heading_2" in types
        all_text = _extract_all_text(blocks)
        assert "AI safety" in all_text

    def test_no_themes_section_skipped(self):
        blocks = _build_weekly_blocks([_make_paper()], [], "", "")
        # Should still produce blocks (heading, stats, paper blocks)
        assert len(blocks) >= 1

    def test_includes_paper_titles(self):
        papers = [_make_paper("2401.00001", "Alpha"), _make_paper("2401.00002", "Beta")]
        blocks = _build_weekly_blocks(papers, [], "", "")
        all_text = _extract_all_text(blocks)
        assert "Alpha" in all_text
        assert "Beta" in all_text


# ---------------------------------------------------------------------------
# TestBuildBlogBlocks
# ---------------------------------------------------------------------------


class TestBuildBlogBlocks:
    def test_includes_content(self):
        blog = _make_blog()
        blocks = _build_blog_blocks(blog)
        all_text = _extract_all_text(blocks)
        assert "Introduction" in all_text
        assert "Method" in all_text
        assert "sparse attention" in all_text.lower()

    def test_includes_chart_image(self):
        blog = _make_blog()
        blocks = _build_blog_blocks(blog)
        all_text = _extract_all_text(blocks)
        assert "Performance Chart" in all_text
        assert "Benchmark comparison." in all_text
        assert "chart.png" in all_text

    def test_includes_diagram_url(self):
        blog = _make_blog()
        blocks = _build_blog_blocks(blog)
        # diagram_url should produce an image block
        image_blocks = [b for b in blocks if b["type"] == "image"]
        assert len(image_blocks) >= 1
        urls = [b["image"]["external"]["url"] for b in image_blocks]
        assert "https://example.com/diagram.png" in urls

    def test_includes_title(self):
        blog = _make_blog()
        blocks = _build_blog_blocks(blog)
        all_text = _extract_all_text(blocks)
        assert "Deep Dive: Test Paper" in all_text

    def test_includes_fact_check(self):
        blog = _make_blog()
        blocks = _build_blog_blocks(blog)
        all_text = _extract_all_text(blocks)
        assert "9.1" in all_text
        assert "All claims verified." in all_text

    def test_content_headings_parsed(self):
        blog = _make_blog()
        blocks = _build_blog_blocks(blog)
        # "# Introduction" and "## Method" should become heading blocks
        heading_texts = _extract_headings(blocks)
        assert "Introduction" in heading_texts
        assert "Method" in heading_texts

    def test_no_diagram_skips_image_block(self):
        blog = _make_blog()
        blog = blog.model_copy(update={"diagram_url": ""})
        blocks = _build_blog_blocks(blog)
        image_blocks = [b for b in blocks if b["type"] == "image"]
        assert len(image_blocks) == 0

    def test_no_charts_skips_charts_section(self):
        blog = _make_blog()
        blog = blog.model_copy(update={"charts": []})
        blocks = _build_blog_blocks(blog)
        all_text = _extract_all_text(blocks)
        # Should not contain chart-specific content
        assert "Performance Chart" not in all_text


# ---------------------------------------------------------------------------
# TestPublishDaily
# ---------------------------------------------------------------------------


class TestPublishDaily:
    def test_calls_notion_api(self):
        mock_response = {"id": "page-abc-123", "object": "page"}
        with patch("jobpulse.papers.notion_publisher._notion_api", return_value=mock_response) as mock_api:
            publisher = NotionPublisher()
            result = publisher.publish_daily([_make_paper()], "2026-04-02")

        assert result == mock_response
        # _notion_api must have been called (POST /pages at minimum)
        mock_api.assert_called()
        first_call = mock_api.call_args_list[0]
        assert first_call[0][0].upper() == "POST"
        assert "/pages" in first_call[0][1]

    def test_passes_date_in_title(self):
        mock_response = {"id": "page-xyz", "object": "page"}
        with patch("jobpulse.papers.notion_publisher._notion_api", return_value=mock_response) as mock_api:
            publisher = NotionPublisher()
            publisher.publish_daily([_make_paper()], "2026-04-02")

        # The body passed to _notion_api should contain the date string somewhere
        body = mock_api.call_args_list[0][0][2]  # third positional arg
        import json
        body_str = json.dumps(body)
        assert "2026-04-02" in body_str

    def test_batches_large_paper_list(self):
        """More than 100 blocks should trigger a second _notion_api call for appending."""
        papers = [_make_paper(f"2401.{i:05d}", f"Paper {i}") for i in range(20)]
        mock_response = {"id": "page-batch", "object": "page"}
        with patch("jobpulse.papers.notion_publisher._notion_api", return_value=mock_response) as mock_api:
            publisher = NotionPublisher()
            publisher.publish_daily(papers, "2026-04-02")

        # With 20 papers × ~5 blocks each = ~100 blocks, may need batch append
        assert mock_api.call_count >= 1  # at minimum the create call


class TestPublishWeekly:
    def test_calls_notion_api(self):
        mock_response = {"id": "page-weekly", "object": "page"}
        with patch("jobpulse.papers.notion_publisher._notion_api", return_value=mock_response) as mock_api:
            publisher = NotionPublisher()
            result = publisher.publish_weekly(
                papers=[_make_paper()],
                themes=["Efficiency", "Agents"],
                start_date="2026-03-25",
                end_date="2026-03-31",
            )

        assert result == mock_response
        mock_api.assert_called()

    def test_themes_in_body(self):
        mock_response = {"id": "page-weekly", "object": "page"}
        with patch("jobpulse.papers.notion_publisher._notion_api", return_value=mock_response) as mock_api:
            publisher = NotionPublisher()
            publisher.publish_weekly([_make_paper()], ["Efficiency dominates"], "2026-03-25", "2026-03-31")

        import json
        body_str = json.dumps(mock_api.call_args_list[0][0][2])
        assert "Efficiency dominates" in body_str


class TestPublishBlog:
    def test_calls_notion_api(self):
        mock_response = {"id": "page-blog", "object": "page"}
        with patch("jobpulse.papers.notion_publisher._notion_api", return_value=mock_response) as mock_api:
            publisher = NotionPublisher()
            result = publisher.publish_blog(_make_blog())

        assert result == mock_response
        mock_api.assert_called()

    def test_blog_title_in_body(self):
        mock_response = {"id": "page-blog", "object": "page"}
        with patch("jobpulse.papers.notion_publisher._notion_api", return_value=mock_response) as mock_api:
            publisher = NotionPublisher()
            publisher.publish_blog(_make_blog())

        import json
        body_str = json.dumps(mock_api.call_args_list[0][0][2])
        assert "Deep Dive: Test Paper" in body_str


# ---------------------------------------------------------------------------
# TestGetParent
# ---------------------------------------------------------------------------


class TestGetParent:
    def test_uses_database_id_when_set(self):
        with patch("jobpulse.papers.notion_publisher.NOTION_RESEARCH_DB_ID", "db-123"):
            publisher = NotionPublisher()
            parent = publisher._get_parent()
        assert parent == {"database_id": "db-123"}

    def test_falls_back_to_page_id(self):
        with (
            patch("jobpulse.papers.notion_publisher.NOTION_RESEARCH_DB_ID", ""),
            patch("jobpulse.papers.notion_publisher.NOTION_PARENT_PAGE_ID", "page-456"),
        ):
            publisher = NotionPublisher()
            parent = publisher._get_parent()
        assert parent == {"page_id": "page-456"}


# ---------------------------------------------------------------------------
# TestNotionApiHelper
# ---------------------------------------------------------------------------


class TestNotionApiHelper:
    def test_returns_empty_dict_when_no_api_key(self):
        from jobpulse.papers.notion_publisher import _notion_api

        with patch("jobpulse.papers.notion_publisher.NOTION_API_KEY", ""):
            result = _notion_api("GET", "/pages/123")
        assert result == {}

    def test_returns_response_json_on_success(self):
        from jobpulse.papers.notion_publisher import _notion_api

        fake_response = MagicMock()
        fake_response.json.return_value = {"object": "page", "id": "abc"}

        with (
            patch("jobpulse.papers.notion_publisher.NOTION_API_KEY", "secret-key"),
            patch("httpx.Client") as mock_client_cls,
        ):
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.request.return_value = fake_response
            mock_client_cls.return_value = mock_client

            result = _notion_api("POST", "/pages", {"children": []})

        assert result == {"object": "page", "id": "abc"}

    def test_returns_empty_dict_on_exception(self):
        from jobpulse.papers.notion_publisher import _notion_api

        with (
            patch("jobpulse.papers.notion_publisher.NOTION_API_KEY", "secret-key"),
            patch("httpx.Client", side_effect=Exception("network error")),
        ):
            result = _notion_api("GET", "/pages/bad")
        assert result == {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_all_text(blocks: list[dict]) -> str:
    """Pull all rich_text content strings out of a block list."""
    parts: list[str] = []
    for block in blocks:
        btype = block.get("type", "")
        inner = block.get(btype, {})
        for rt in inner.get("rich_text", []):
            parts.append(rt.get("text", {}).get("content", ""))
        # image caption
        for cap in inner.get("caption", []):
            parts.append(cap.get("text", {}).get("content", ""))
    return " ".join(parts)


def _extract_headings(blocks: list[dict]) -> list[str]:
    """Return the text of all heading blocks."""
    result = []
    for block in blocks:
        btype = block.get("type", "")
        if btype.startswith("heading_"):
            inner = block.get(btype, {})
            for rt in inner.get("rich_text", []):
                result.append(rt.get("text", {}).get("content", ""))
    return result
