from research_journal.delivery import build_journal_telegram_digest
from jobpulse.papers.models import RankedPaper
from research_journal.models import VerificationBadge


def _rp(arxiv_id: str, title: str, score: float = 8.0) -> RankedPaper:
    return RankedPaper(
        arxiv_id=arxiv_id, title=title, authors=["A"], abstract="abs",
        categories=["cs.CL"], pdf_url="", arxiv_url=f"https://arxiv.org/abs/{arxiv_id}",
        published_at="2026-01-01", impact_score=score, summary="",
        rank_reason="novel attention pattern, working repo with 1.2k stars",
    )


def test_digest_lists_core_only_in_main_body():
    badge = VerificationBadge(has_results=True, peer_reviewed=True, has_repo=True,
                              independent_citations=True, claims_grounded=True)
    items = [
        (_rp("x", "Core Paper A"), badge, "core"),
        (_rp("y", "Core Paper B"), badge, "core"),
        (_rp("z", "Tangent Paper"), badge, "tangent"),
    ]
    msg = build_journal_telegram_digest(items, page_url_for=lambda aid: f"https://notion.so/{aid}")
    assert "Core Paper A" in msg
    assert "Core Paper B" in msg
    assert "Tangent Paper" not in msg.split("+ 1 tangent")[0]
    assert "tangent" in msg.lower()


def test_digest_uses_emoji_badges():
    badge = VerificationBadge(has_results=True, peer_reviewed=False, has_repo=True,
                              independent_citations=False, claims_grounded=True)
    items = [(_rp("x", "X"), badge, "core")]
    msg = build_journal_telegram_digest(items, page_url_for=lambda aid: "u")
    assert "3/5" in msg or "🟢🟢🟢" in msg
