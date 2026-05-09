from research_journal.models import (
    DomainTag, BenchResult, ExtractedFacts, VerificationBadge, PaperTypeClassification,
)


def test_domain_tag_literal():
    assert DomainTag.__args__ == ("core", "tangent", "out")


def test_bench_result_round_trip():
    b = BenchResult(name="MMLU", metric="accuracy", value=72.3, baseline=68.1)
    assert b.delta == 4.2


def test_verification_badge_score_property():
    badge = VerificationBadge(
        has_results=True, peer_reviewed=False, has_repo=True,
        independent_citations=False, claims_grounded=True,
        reasons={"peer_reviewed": "venue 'arxiv preprint' not in PEER_REVIEWED_VENUES"},
    )
    assert badge.score == 3


def test_paper_type_classification_includes_has_results():
    pt = PaperTypeClassification(
        has_results=True, paper_type="research",
        reason="benchmarks MMLU + ablation table", confidence=0.92,
    )
    assert pt.has_results is True
    assert pt.paper_type == "research"


def test_extracted_facts_requires_excerpts():
    """raw_excerpts is load-bearing for the hallucination guard — must be non-empty."""
    facts = ExtractedFacts(
        problem="x", method_steps=["a"], architecture_details={"k": "v"},
        benchmarks=[], ablations=[], limitations=[], key_insight="z",
        raw_excerpts=["a verbatim quote from the paper"],
    )
    assert len(facts.raw_excerpts) >= 1
