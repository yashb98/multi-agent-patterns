import pytest
from jobpulse.papers.ranker import _lab_boost
from jobpulse.papers.models import Paper


def _paper(authors: list[str], affiliations: list[str] | None = None) -> Paper:
    p = Paper(arxiv_id="0", title="t", authors=authors, abstract="a",
              categories=["cs.CL"], pdf_url="", arxiv_url="", published_at="2026-01-01")
    if affiliations is not None:
        p.affiliations = affiliations  # type: ignore[attr-defined]
    return p


def test_no_recognized_lab_zero_boost():
    assert _lab_boost(_paper(["Random Researcher"], ["University of Foo"])) == 0.0


def test_one_recognized_lab_returns_05():
    p = _paper(["A", "B"], ["Some College", "Anthropic"])
    assert _lab_boost(p) == pytest.approx(0.5)


def test_first_author_recognized_returns_1():
    p = _paper(["A", "B"], ["DeepMind", "Some College"])
    assert _lab_boost(p) == pytest.approx(1.0)


def test_two_recognized_labs_returns_15():
    p = _paper(["A", "B"], ["Anthropic", "Stanford"])
    assert _lab_boost(p) == pytest.approx(1.5)


def test_fuzzy_match_handles_abbreviation():
    p = _paper(["A"], ["Meta AI Research, FAIR"])
    assert _lab_boost(p) >= 0.5  # FAIR matches via Levenshtein
