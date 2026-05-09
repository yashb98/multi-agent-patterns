from research_journal.summarizer import is_claim_grounded
from research_journal.models import ExtractedFacts, BenchResult


def _facts(excerpts: list[str], benches: list[BenchResult] | None = None) -> ExtractedFacts:
    return ExtractedFacts(
        problem="x", method_steps=["s"], architecture_details={},
        benchmarks=benches or [], ablations=[], limitations=[],
        key_insight="k", raw_excerpts=excerpts,
    )


def test_substring_match_grounds():
    facts = _facts(["the model achieves 72.3% on MMLU and 84.1 F1 on BoolQ"])
    assert is_claim_grounded("MoA achieves 72.3% on MMLU.", facts) is True


def test_numeric_match_in_benchmark():
    facts = _facts(
        excerpts=["table not in excerpts"],
        benches=[BenchResult(name="GSM8K", metric="acc", value=89.7)],
    )
    assert is_claim_grounded("On GSM8K, accuracy reaches 89.7%.", facts) is True


def test_unrelated_claim_fails():
    facts = _facts(["totally different content"])
    # Without an embedder hook, embedding similarity is rejected — check the deterministic
    # paths (substring + numeric extraction) only here.
    assert is_claim_grounded("Trained on 1.5T tokens.", facts) is False


def test_embedding_similarity_threshold(monkeypatch):
    facts = _facts(["paraphrased version"])
    monkeypatch.setattr(
        "research_journal.summarizer._embedding_similarity",
        lambda a, b: 0.91,
    )
    assert is_claim_grounded("Different wording but same meaning.", facts) is True
