from unittest.mock import patch

from jobpulse.papers.models import RankedPaper
from research_journal.models import VerificationBadge
from research_journal.delivery import publish_journal_to_notion


def test_publish_journal_creates_one_page_per_paper():
    paper = RankedPaper(
        arxiv_id="x", title="Title", authors=["A"], abstract="abs",
        categories=["cs.CL"], pdf_url="", arxiv_url="https://arxiv.org/abs/x",
        published_at="2026-01-01", impact_score=8.0, summary="s",
    )
    badge = VerificationBadge(
        has_results=True, peer_reviewed=True, has_repo=True,
        independent_citations=False, claims_grounded=True,
    )

    with patch("research_journal.delivery.notion_api") as mock_api:
        mock_api.return_value = {"id": "page-x"}

        publish_journal_to_notion(
            items=[(paper, badge, "## TL;DR\nfoo\n\n## Problem\nbar\n", "core")],
            digest_date="2026-05-09",
        )

        assert mock_api.call_count == 1
        _method, _endpoint, payload = mock_api.call_args[0]
        props = payload["properties"]
        assert props["Title"]["title"][0]["text"]["content"] == "Title"
        assert props["Domain tag"]["select"]["name"] == "core"
        assert props["Badge"]["number"] == 4
