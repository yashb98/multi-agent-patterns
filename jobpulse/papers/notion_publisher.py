"""NotionPublisher — creates Notion pages for daily digests, weekly summaries, and blog posts."""

from __future__ import annotations

from jobpulse.config import NOTION_PARENT_PAGE_ID, NOTION_RESEARCH_DB_ID
from jobpulse.papers.models import BlogPost, RankedPaper
from jobpulse.notion_client import notion_api as _notion_api
from shared.logging_config import get_logger

logger = get_logger(__name__)

_BLOCK_BATCH = 100  # Notion API limit per append call


def _text_block(text: str, block_type: str = "paragraph") -> dict:
    """Build a simple rich-text Notion block."""
    return {
        "object": "block",
        "type": block_type,
        block_type: {
            "rich_text": [{"type": "text", "text": {"content": text[:2000]}}]
        },
    }


def _heading_block(text: str, level: int = 2) -> dict:
    """Build a heading block (level 1, 2, or 3)."""
    level = max(1, min(3, level))
    block_type = f"heading_{level}"
    return {
        "object": "block",
        "type": block_type,
        block_type: {
            "rich_text": [{"type": "text", "text": {"content": text[:2000]}}]
        },
    }


def _divider_block() -> dict:
    return {"object": "block", "type": "divider", "divider": {}}


def _image_block(url: str, caption: str = "") -> dict:
    block: dict = {
        "object": "block",
        "type": "image",
        "image": {
            "type": "external",
            "external": {"url": url},
        },
    }
    if caption:
        block["image"]["caption"] = [{"type": "text", "text": {"content": caption[:2000]}}]
    return block


# ---------------------------------------------------------------------------
# Block builders (pure, no I/O — easy to unit-test)
# ---------------------------------------------------------------------------


def _build_paper_blocks(paper: RankedPaper) -> list[dict]:
    """Build Notion blocks for a single ranked paper."""
    blocks: list[dict] = []
    title_line = f"[{paper.category_tag}] {paper.title} — {paper.impact_score:.1f}"
    blocks.append(_heading_block(title_line, level=3))

    if paper.summary:
        blocks.append(_text_block(paper.summary))

    detail_parts = []
    if paper.key_technique:
        detail_parts.append(f"Technique: {paper.key_technique}")
    if paper.practical_takeaway:
        detail_parts.append(f"Takeaway: {paper.practical_takeaway}")
    if detail_parts:
        blocks.append(_text_block(" | ".join(detail_parts)))

    link_line = paper.arxiv_url
    if paper.hf_upvotes:
        link_line += f"  |  HF upvotes: {paper.hf_upvotes}"
    if paper.linked_models:
        link_line += f"  |  {len(paper.linked_models)} model(s)"
    blocks.append(_text_block(link_line))

    if paper.fact_check:
        fc = paper.fact_check
        blocks.append(_text_block(f"Fact-check: {fc.score:.3f}/10 ({fc.verified_count}/{fc.total_claims} claims verified)"))

    blocks.append(_divider_block())
    return blocks


def _build_daily_blocks(papers: list[RankedPaper], digest_date: str) -> list[dict]:
    """Build the full block list for a daily digest page."""
    blocks: list[dict] = [
        _heading_block(f"Daily Paper Digest — {digest_date}", level=1),
        _text_block(f"{len(papers)} paper(s) ranked by impact score."),
        _divider_block(),
    ]
    for paper in papers:
        blocks.extend(_build_paper_blocks(paper))
    return blocks


def _build_weekly_blocks(
    papers: list[RankedPaper],
    themes: list[str],
    start_date: str,
    end_date: str,
) -> list[dict]:
    """Build the full block list for a weekly summary page."""
    label = f"{start_date} – {end_date}" if start_date and end_date else "Weekly Summary"
    blocks: list[dict] = [
        _heading_block(f"Weekly Paper Summary — {label}", level=1),
        _text_block(f"{len(papers)} paper(s) reviewed this week."),
        _divider_block(),
    ]

    if themes:
        blocks.append(_heading_block("Themes", level=2))
        for theme in themes:
            blocks.append(_text_block(f"• {theme}"))
        blocks.append(_divider_block())

    blocks.append(_heading_block("Papers", level=2))
    for paper in papers:
        blocks.extend(_build_paper_blocks(paper))

    return blocks


def _build_blog_blocks(blog: BlogPost) -> list[dict]:
    """Build the full block list for a blog post page."""
    blocks: list[dict] = [
        _heading_block(blog.title, level=1),
        _text_block(f"Generated: {blog.generated_at}  |  Words: {blog.word_count}  |  GRPO score: {blog.grpo_score:.1f}"),
        _divider_block(),
    ]

    # Diagram / architecture image
    if blog.diagram_url:
        blocks.append(_heading_block("Architecture Diagram", level=2))
        blocks.append(_image_block(blog.diagram_url, caption="Architecture diagram"))
        blocks.append(_divider_block())

    # Charts
    if blog.charts:
        blocks.append(_heading_block("Charts", level=2))
        for chart in blog.charts:
            blocks.append(_heading_block(chart.title, level=3))
            if chart.description:
                blocks.append(_text_block(chart.description))
            # png_path is a local path; we embed it as text reference (can be swapped for upload)
            blocks.append(_text_block(f"Chart file: {chart.png_path}"))
        blocks.append(_divider_block())

    # Blog content split into paragraphs / headings
    if blog.content:
        blocks.append(_heading_block("Content", level=2))
        for line in blog.content.split("\n"):
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("## "):
                blocks.append(_heading_block(stripped[3:], level=2))
            elif stripped.startswith("# "):
                blocks.append(_heading_block(stripped[2:], level=1))
            elif stripped.startswith("### "):
                blocks.append(_heading_block(stripped[4:], level=3))
            else:
                blocks.append(_text_block(stripped))

    # Fact-check summary
    if blog.fact_check:
        fc = blog.fact_check
        blocks.append(_divider_block())
        blocks.append(_heading_block("Fact-Check", level=2))
        blocks.append(_text_block(f"Score: {fc.score:.3f}/10 — {fc.verified_count}/{fc.total_claims} claims verified"))
        if fc.explanation:
            blocks.append(_text_block(fc.explanation))

    return blocks


# ---------------------------------------------------------------------------
# NotionPublisher
# ---------------------------------------------------------------------------


class NotionPublisher:
    """Publishes paper digests, weekly summaries, and blog posts to Notion."""

    def _get_parent(self) -> dict:
        """Return the Notion parent config for new pages.

        Prefers NOTION_RESEARCH_DB_ID (database parent) so pages appear in the
        research database.  Falls back to NOTION_PARENT_PAGE_ID (page parent).
        """
        if NOTION_RESEARCH_DB_ID:
            return {"database_id": NOTION_RESEARCH_DB_ID}
        return {"page_id": NOTION_PARENT_PAGE_ID}

    def _create_page(self, title: str, blocks: list[dict]) -> dict:
        """Create a Notion page with a title property and initial blocks.

        Blocks beyond the first 100 are appended in subsequent batches.
        """
        parent = self._get_parent()
        title_property: dict

        # Database parents need a properties object with a "Name" title field.
        # Page parents use a title array directly.
        if "database_id" in parent:
            title_property = {
                "Title": {
                    "title": [{"type": "text", "text": {"content": title}}]
                }
            }
        else:
            title_property = {
                "title": [{"type": "text", "text": {"content": title}}]
            }

        first_batch = blocks[:_BLOCK_BATCH]
        payload: dict = {
            "parent": parent,
            "properties": title_property,
            "children": first_batch,
        }
        result = _notion_api("POST", "/pages", payload)

        page_id = result.get("id")
        if not page_id:
            return result

        # Append remaining blocks in batches
        remaining = blocks[_BLOCK_BATCH:]
        offset = 0
        while offset < len(remaining):
            batch = remaining[offset : offset + _BLOCK_BATCH]
            _notion_api("PATCH", f"/blocks/{page_id}/children", {"children": batch})
            offset += _BLOCK_BATCH

        return result

    def publish_daily(self, papers: list[RankedPaper], digest_date: str) -> dict:
        """Create a daily digest page in Notion.

        Args:
            papers: Ranked papers to include.
            digest_date: ISO date string (e.g. "2026-04-02").

        Returns:
            Notion API response dict (contains ``id`` on success).
        """
        blocks = _build_daily_blocks(papers, digest_date)
        title = f"Paper Digest — {digest_date}"
        logger.info("Publishing daily digest (%d papers) to Notion", len(papers))
        return self._create_page(title, blocks)

    def publish_weekly(
        self,
        papers: list[RankedPaper],
        themes: list[str],
        start_date: str = "",
        end_date: str = "",
    ) -> dict:
        """Create a weekly summary page in Notion.

        Args:
            papers: All ranked papers from the week.
            themes: High-level theme strings extracted from the week.
            start_date: ISO date of the first day (optional).
            end_date: ISO date of the last day (optional).

        Returns:
            Notion API response dict.
        """
        blocks = _build_weekly_blocks(papers, themes, start_date, end_date)
        label = f"{start_date} – {end_date}" if start_date and end_date else "Summary"
        title = f"Weekly Paper Summary — {label}"
        logger.info("Publishing weekly summary (%d papers, %d themes) to Notion", len(papers), len(themes))
        return self._create_page(title, blocks)

    def publish_blog(self, blog: BlogPost) -> dict:
        """Create a blog post page in Notion.

        Args:
            blog: The generated blog post, including charts and diagram URL.

        Returns:
            Notion API response dict.
        """
        blocks = _build_blog_blocks(blog)
        logger.info("Publishing blog post '%s' to Notion", blog.title)
        return self._create_page(blog.title, blocks)
