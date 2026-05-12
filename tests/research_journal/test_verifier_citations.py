from research_journal.verifier import check_independent_citations


def test_passes_with_3_independent(monkeypatch):
    monkeypatch.setattr(
        "research_journal.verifier._fetch_citing_paper_labs",
        lambda arxiv_id, author_labs: ["LabA", "LabB", "LabC", "LabD"],
    )
    ok, reason = check_independent_citations("2401.06401", author_labs={"LabX"})
    assert ok is True


def test_fails_with_2(monkeypatch):
    monkeypatch.setattr(
        "research_journal.verifier._fetch_citing_paper_labs",
        lambda arxiv_id, author_labs: ["LabA", "LabB"],
    )
    ok, _ = check_independent_citations("2401.99999", author_labs={"LabX"})
    assert ok is False


def test_s2_failure_returns_unknown(monkeypatch):
    monkeypatch.setattr(
        "research_journal.verifier._fetch_citing_paper_labs",
        lambda arxiv_id, author_labs: (_ for _ in ()).throw(RuntimeError("S2 down")),
    )
    ok, _ = check_independent_citations("2401.99999", author_labs={"LabX"})
    assert ok is None
