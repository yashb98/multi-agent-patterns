from research_journal.verifier import verify_paper
from jobpulse.papers.models import Paper


def test_compose_aggregates_5_checks(monkeypatch):
    monkeypatch.setattr("research_journal.verifier.check_peer_reviewed",
                        lambda aid: (True, "NeurIPS"))
    monkeypatch.setattr("research_journal.verifier.check_has_repo",
                        lambda url, cache=None: (True, "320 stars", "2026-04-25T00:00:00Z"))
    monkeypatch.setattr("research_journal.verifier.check_independent_citations",
                        lambda aid, labs: (True, "5 labs"))

    paper = Paper(arxiv_id="2401.06401", title="t", authors=["A"], abstract="a",
                  categories=["cs.CL"], pdf_url="", arxiv_url="", published_at="2026-01-01",
                  github_url="https://github.com/x/y")
    badge = verify_paper(paper, has_results=True)
    assert badge.score == 4   # has_results, peer_reviewed, has_repo, indep_citations (claims_grounded set later)
    assert badge.peer_reviewed is True
    assert badge.has_repo is True


def test_compose_handles_unknown(monkeypatch):
    """API-unavailable checks contribute False (not True), but stored in `reasons`."""
    monkeypatch.setattr("research_journal.verifier.check_peer_reviewed",
                        lambda aid: (None, "S2 down"))
    monkeypatch.setattr("research_journal.verifier.check_has_repo",
                        lambda url, cache=None: (None, "GitHub down", ""))
    monkeypatch.setattr("research_journal.verifier.check_independent_citations",
                        lambda aid, labs: (None, "S2 down"))

    paper = Paper(arxiv_id="x", title="t", authors=["A"], abstract="a",
                  categories=["cs.CL"], pdf_url="", arxiv_url="", published_at="2026-01-01")
    badge = verify_paper(paper, has_results=True)
    assert badge.peer_reviewed is False
    assert "unknown" in badge.reasons["peer_reviewed"].lower() or "down" in badge.reasons["peer_reviewed"].lower()
