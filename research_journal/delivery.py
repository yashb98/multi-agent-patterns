"""Journal-specific delivery: Notion page composition.

Posts one page per paper to the Notion research database.
Uses jobpulse.notion_client.notion_api for all Notion HTTP calls and
jobpulse.config.NOTION_RESEARCH_DB_ID as the target database (no separate
journal DB env var exists in v1).
"""

from __future__ import annotations

from jobpulse.config import NOTION_RESEARCH_DB_ID
from jobpulse.notion_client import notion_api
from jobpulse.papers.models import RankedPaper
from research_journal.models import VerificationBadge
from shared.logging_config import get_logger

logger = get_logger(__name__)


def publish_journal_to_notion(
    items: list[tuple],  # (RankedPaper, VerificationBadge, summary_md, domain_tag)
    digest_date: str,
) -> list[str]:
    """Create one Notion page per item in the research database. Returns page IDs."""
    page_ids: list[str] = []
    for paper, badge, summary_md, domain_tag in items:
        props = {
            "Title": {"title": [{"text": {"content": paper.title[:200]}}]},
            "Date": {"date": {"start": digest_date}},
            "Domain tag": {"select": {"name": domain_tag}},
            "Badge": {"number": badge.score},
            "Badge breakdown": {"multi_select": [
                {"name": k} for k, v in {
                    "has_results": badge.has_results,
                    "peer_reviewed": badge.peer_reviewed,
                    "has_repo": badge.has_repo,
                    "independent_citations": badge.independent_citations,
                    "claims_grounded": badge.claims_grounded,
                }.items() if v
            ]},
            "Rank reason": {"rich_text": [{"text": {"content": getattr(paper, "rank_reason", "")[:2000]}}]},
            "Authors": {"rich_text": [{"text": {"content": ", ".join(paper.authors[:8])[:2000]}}]},
            "arXiv link": {"url": paper.arxiv_url or None},
            "Repo link": {"url": getattr(paper, "github_url", "") or None},
            "Read": {"checkbox": False},
            "Saved for impl": {"checkbox": False},
        }
        children = _summary_to_blocks(summary_md)
        try:
            resp = notion_api(
                "POST",
                "/pages",
                {
                    "parent": {"database_id": NOTION_RESEARCH_DB_ID},
                    "properties": props,
                    "children": children,
                },
            )
            page_id = resp.get("id")
            if page_id:
                page_ids.append(page_id)
        except Exception as exc:
            logger.warning("Notion page create failed for %s: %s", paper.arxiv_id, exc)
    return page_ids


def _summary_to_blocks(md: str) -> list[dict]:
    """Convert the 6-section markdown summary into heading_2 + paragraph blocks."""
    blocks: list[dict] = []
    for line in md.splitlines():
        line = line.rstrip()
        if not line:
            continue
        if line.startswith("## "):
            blocks.append({
                "object": "block", "type": "heading_2",
                "heading_2": {"rich_text": [{"type": "text", "text": {"content": line[3:]}}]},
            })
        else:
            blocks.append({
                "object": "block", "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": line[:1900]}}]},
            })
    return blocks


def build_journal_telegram_digest(
    items: list[tuple],   # (RankedPaper, VerificationBadge, domain_tag)
    page_url_for,         # callable: arxiv_id -> Notion page URL
) -> str:
    """Build Telegram-formatted message from research journal items.

    Lists core papers in main body with verification badges and rank reasons.
    Collapses tangent papers at the end.
    """
    core = [i for i in items if i[2] == "core"]
    tangent = [i for i in items if i[2] == "tangent"]

    lines = [f"🧪 Daily Research Journal — {len(core)} papers ({len(tangent)} tangent)\n"]
    for paper, badge, _ in core:
        emoji = "🟢" * badge.score + "⚪" * (5 - badge.score)
        url = page_url_for(paper.arxiv_id)
        reason = (paper.rank_reason or "").strip()
        lines.append(f"{emoji} {badge.score}/5 — {paper.title}\n  {reason[:120]}\n  → {url}\n")
    if tangent:
        lines.append(f"\n+ {len(tangent)} tangent papers in Notion\n")
    return "".join(lines)
