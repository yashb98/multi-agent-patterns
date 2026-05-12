from research_journal.verifier import check_peer_reviewed


def test_neurips_venue_passes(monkeypatch):
    monkeypatch.setattr(
        "research_journal.verifier.semantic_scholar_lookup",
        lambda arxiv_id: {"venue": "NeurIPS 2025", "is_peer_reviewed": True},
    )
    ok, reason = check_peer_reviewed("2401.06401")
    assert ok is True
    assert "NeurIPS" in reason


def test_arxiv_only_fails(monkeypatch):
    monkeypatch.setattr(
        "research_journal.verifier.semantic_scholar_lookup",
        lambda arxiv_id: {"venue": "arXiv", "is_peer_reviewed": False},
    )
    ok, reason = check_peer_reviewed("2401.99999")
    assert ok is False


def test_s2_unavailable_returns_unknown(monkeypatch):
    monkeypatch.setattr("research_journal.verifier.semantic_scholar_lookup", lambda x: None)
    ok, reason = check_peer_reviewed("2401.99999")
    assert ok is None
    assert "unavailable" in reason.lower()
